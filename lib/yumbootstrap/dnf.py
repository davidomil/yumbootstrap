import os
import shutil

import yumbootstrap.sh as sh
import yumbootstrap.fs as fs

import logging

logger = logging.getLogger("dnf")


# -----------------------------------------------------------------------------

def mklist(value):
    if isinstance(value, list):
        return value
    elif isinstance(value, tuple):
        return list(value)
    else:
        return [value]


# -----------------------------------------------------------------------------

class DnfConfig:
    def __init__(self, chroot, repos={}, env=None, release=""):
        self.chroot = os.path.abspath(chroot)
        self.repos = repos.copy()  # shallow copy is enough
        self.gpg_keys = os.path.join(self.chroot, 'yumbootstrap/RPM-GPG-KEYS')
        self.pretend_has_keys = False
        # self.multilib = False
        self.env = env
        self.release = release

    def add_repository(self, name, url):
        self.repos[name] = url

    def add_key(self, path, pretend=False):
        if pretend:
            self.pretend_has_keys = True
        else:
            fs.touch(self.gpg_keys)
            open(self.gpg_keys, 'a').write(open(path).read())

    @property
    def config_file(self):
        return os.path.join(self.chroot, 'yumbootstrap/dnf.conf')

    @property
    def root_dir(self):
        return os.path.join(self.chroot, 'yumbootstrap')

    def text(self):
        if self.pretend_has_keys or os.path.exists(self.gpg_keys):
            logger.info("GPG keys defined, adding them to repository configs")
            gpgcheck = 1

            def repo(name, url, release):
                return \
                    '\n' \
                    '[%s]\n' \
                    'name = %s\n' \
                    'baseurl = %s\n' \
                    'releasever = %s\n' \
                    'gpgkey = file://%s\n' % (name, name, url, release, self.gpg_keys)
        else:
            logger.warn("no GPG keys defined, RPM signature verification disabled")
            gpgcheck = 0

            def repo(name, url, release):
                return \
                    '\n' \
                    '[%s]\n' \
                    'name = %s\n' \
                    'baseurl = %s\n' \
                    'releasever = %s\n' % (name, name, url, release)

        main = \
            '[main]\n' \
            'exactarch = 1\n' \
            'obsoletes = 1\n' \
            '#multilib_policy = all | best\n' \
            'cachedir = /yumbootstrap/cache\n' \
            'logfile  = /yumbootstrap/log/dnf.log\n'
        main += 'gpgcheck = %d\n' % (gpgcheck)
        main += 'reposdir = %s/yumbootstrap/dnf.repos.d\n' % (gpgcheck)
        logger.info(f"release = {self.release}")
        repos = [repo(name, self.repos[name], self.release) for name in sorted(self.repos)]

        return main + ''.join(repos)


# -----------------------------------------------------------------------------

# TODO:
#   * setarch
#   * should `chroot' go through YumConfig?
class Dnf:
    def __init__(self, chroot, dnf_conf=None, dnf='/usr/bin/dnf',
                 interactive=False):
        self.chroot = os.path.abspath(chroot)
        if dnf_conf is not None:
            self.dnf_conf = dnf_conf
        else:
            self.dnf_conf = DnfConfig(chroot=chroot)
        self.dnf = dnf  # dnf from host OS
        self.interactive = interactive
        self.rpmdb_fixed = False
        # NOTE: writing dnf.conf is delayed to the first operation

    def _dnf_call(self):
        dnf_conf = self.dnf_conf.config_file

        if not os.path.exists(dnf_conf):
            logger.info("%s doesn't exist, creating one", dnf_conf)
            fs.touch(dnf_conf, text=self.dnf_conf.text())

        opts = [self.dnf, '-c', dnf_conf, '--installroot', self.chroot, '-y', '--releasever=', self.dnf_conf.release]

        if self.interactive:
            opts.extend(['-e', '1', '-d', '2'])
        else:
            opts.extend(['-e', '1', '-d', '1'])

        return opts

    def install(self, packages, exclude=[]):
        if self.rpmdb_fixed:
            raise Exception("Can't install anything after RPM DB was fixed")

        exclude_opts = ["--exclude=" + pkg for pkg in exclude]

        sh.run(
            self._dnf_call() + exclude_opts + ['install'] + mklist(packages),
            env=self.dnf_conf.env,
        )

    def group_install(self, groups, exclude=[]):
        if self.rpmdb_fixed:
            raise Exception("Can't install anything after RPM DB was fixed")

        exclude_opts = ["--exclude=" + pkg for pkg in exclude]

        sh.run(
            self._dnf_call() + exclude_opts + ['groupinstall'] + mklist(groups),
            env=self.dnf_conf.env,
        )

    def clean(self):
        logger.info("removing directory %s", self.dnf_conf.root_dir)
        shutil.rmtree(self.dnf_conf.root_dir, ignore_errors=True)

    def fix_releasever(self):
        logger.info("Fixing releasever for yum")

        sh.run(
            self._dnf_call() + ['install', 'system-release'],
            env=self.dnf_conf.env,
        )

    def fix_rpmdb(self, expected_rpmdb_dir=None,
                  db_load='db_load', rpm='rpm'):
        logger.info("fixing RPM database for guest")

        # use platform-python if available to read rpm dbpath
        if os.path.exists('/usr/libexec/platform-python'):
            platform_python = '/usr/libexec/platform-python'
        else:
            platform_python = 'python'

        current_rpmdb_dir = sh.run(
            [platform_python, '-c', 'import rpm; print(rpm.expandMacro("%{_dbpath}"))'],
            pipe=sh.READ,
            env=self.dnf_conf.env,
        ).strip()

        if expected_rpmdb_dir is None:
            expected_rpmdb_dir = sh.run(
                ['/usr/libexec/platform-python', '-c', 'import rpm; print(rpm.expandMacro("%{_dbpath}"))'],
                chroot=self.chroot,
                pipe=sh.READ,
                env=self.dnf_conf.env,
            ).strip()

        # input directory
        rpmdb_dir = os.path.join(self.chroot, current_rpmdb_dir.lstrip('/'))

        logger.info('converting "Packages" file')

        in_pkg_db = os.path.join(rpmdb_dir, 'Packages')
        tmp_pkg_db = os.path.join(expected_rpmdb_dir, 'Packages.tmp')
        out_pkg_db = os.path.join(expected_rpmdb_dir, 'Packages')

        rpm_database_fix = sh.run(['rpmdb', '--rebuilddb'], chroot=self.chroot, pipe=sh.READ,
                                  env=self.dnf_conf.env).strip()

        logger.info(rpm_database_fix)

        in_command = sh.run(
            ['db_dump', in_pkg_db],
            pipe=sh.READ,
            env=self.dnf_conf.env,
        )
        out_command = sh.run(
            [db_load, tmp_pkg_db],
            chroot=self.chroot, pipe=sh.WRITE,
            env=self.dnf_conf.env,
        )
        for line in in_command:
            out_command.write(line)
        out_command.close()

        os.rename(
            os.path.join(self.chroot, tmp_pkg_db.lstrip('/')),
            os.path.join(self.chroot, out_pkg_db.lstrip('/'))
        )

        logger.info('removing all the files except "Packages"')
        for f in os.listdir(rpmdb_dir):
            if f in ('.', '..', 'Packages'): continue
            os.unlink(os.path.join(rpmdb_dir, f))

        logger.info("running `rpm --rebuilddb'")
        sh.run(
            [rpm, '--rebuilddb'],
            chroot=self.chroot,
            env=self.dnf_conf.env,
        )

        if current_rpmdb_dir != expected_rpmdb_dir:
            # Red Hat under Debian; delete old directory (~/.rpmdb possibly)
            logger.info("removing old RPM DB directory: $TARGET%s",
                        current_rpmdb_dir)
            shutil.rmtree(os.path.join(self.chroot, current_rpmdb_dir.lstrip('/')))

        self.rpmdb_fixed = True

# -----------------------------------------------------------------------------
# vim:ft=python
