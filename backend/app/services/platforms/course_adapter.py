from .base import PlatformAdapter
from .catalog import platform_spec


class CoursePlatformAdapter(PlatformAdapter):
    def __init__(self, name):
        self.name, self.label = name, platform_spec(name)["label"]

    def start_login(self, database, account_id, settings):
        from ..platform_auth import start_login
        return start_login(database, account_id, settings)

    def login_status(self, account_id):
        from ..platform_auth import login_status
        return login_status(account_id)

    async def refresh_session(self, database, account_id):
        from ..platform_auth import refresh_session
        return await refresh_session(database, account_id)

    async def collect_assignments(self, database, account_id, academic_year, run_id=None, progress=None):
        raise ValueError(f"{self.label}已接入网课业务，请在网课页面采集课程")
