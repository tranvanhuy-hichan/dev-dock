import sys

from . import cli

rc = cli.main(sys.argv[1:])
if rc is not None:  # lệnh không cần giao diện (vd. --add-services)
    sys.exit(rc)

from .app import main  # noqa: E402

sys.exit(main())
