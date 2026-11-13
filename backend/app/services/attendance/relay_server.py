from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from .relay import RelayStore, relay_app


def create_app():
    data_file = Path(os.environ.get('MUMU_RELAY_DB', '/var/lib/mumu-attendance/relay.sqlite3'))
    encryption_key = os.environ.get('MUMU_SHARE_ENC_KEY', '')
    admin_secret = os.environ.get('MUMU_SHARE_ADMIN_SECRET', '')
    return relay_app(RelayStore(data_file, encryption_key), admin_secret)


app = create_app()


if __name__ == '__main__':
    uvicorn.run(
        app,
        host=os.environ.get('MUMU_RELAY_HOST', '127.0.0.1'),
        port=int(os.environ.get('MUMU_RELAY_PORT', '8878')),
        proxy_headers=True,
        forwarded_allow_ips='127.0.0.1',
        server_header=False,
        access_log=False,
    )
