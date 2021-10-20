#!/usr/bin/python3

import os
import time
import logging
import yumbootstrap.dnf
import yumbootstrap.log

#-----------------------------------------------------------------------------

logger = logging.getLogger()
logger.addHandler(yumbootstrap.log.ProgressHandler())
if os.environ['VERBOSE'] == 'true':
  logger.setLevel(logging.INFO)

#-----------------------------------------------------------------------------

dnf = yumbootstrap.dnf.Dnf(chroot = os.environ['TARGET'])

# to prevent yumbootstrap.dnf.Dnf from running Python in chroot $TARGET
# one may specify `expected_rpmdb_dir' manually:
#   dnf.fix_rpmdb(expected_rpmdb_dir = '/var/lib/rpm')
# if /usr/bin/db_load has a different name, this also could be provided:
#   dnf.fix_rpmdb(db_load = '/usr/bin/db_load')
dnf.fix_rpmdb()

#-----------------------------------------------------------------------------
# vim:ft=python
