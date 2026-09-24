"""Copy the repo's live-uvicorn conformance tests, changing ONLY the live_url fixture so the
unmodified test bodies drive rvproto's base URL instead of an in-process v1 app."""

import re
import sys
from pathlib import Path

src = Path(sys.argv[1]).read_text()
dst = Path(sys.argv[2])
fixture = re.compile(r'@pytest\.fixture\(scope="module"\)\ndef live_url\(\):.*?thread\.join\(timeout=5\)\n', re.S)
assert fixture.search(src), "live_url fixture not found"
new = fixture.sub(
    '@pytest.fixture(scope="module")\ndef live_url():\n'
    '    import os\n    yield os.environ["AMF_CONFORMANCE_BASE_URL"].rstrip("/")\n', src)
dst.write_text("# ADAPTED COPY: only the live_url fixture differs from the repo file.\n" + new)
print("adapted", dst)
