"""Allow ``python -m sql_query_doctor``."""

import sys

from .cli import main

sys.exit(main())
