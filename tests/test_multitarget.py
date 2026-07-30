import asyncio
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from core.config import config_mgr
from core.engine import (
    DashboardLoginRequired,
    MAX_CONCURRENT_TARGETS,
    TARGETS_VERSION,
    WBLEScanner,
    canonical_dashboard_target_url,
    course_state_key,
    dashboard_target_id,
    dashboard_target_label,
    get_dashboard_targets,
    load_target_auth_state,
    normalize_dashboard_targets,
    register_dashboard_target,
    save_target_auth_state,
    target_auth_state_path,
    unique_course_folder_name,
)


class FakeMultiTargetScanner(WBLEScanner):
    def __init__(self):
        super().__init__()
        self.visited = []

    async def scan_dashboard_target(self, target, _state_db):
        self.visited.append(target["id"])
        if target["label"] == "Faculty B":
            raise DashboardLoginRequired(target)
        course_key = course_state_key(
            "https://wble-a.utar.edu.my/course/view.php?id=10"
        )
        return (
            [{
                "course": "UCCD2063 AI",
                "course_key": course_key,
                "target": target["label"],
                "summary": "updated",
                "files_count": 0,
            }],
            [{
                "key": course_key,
                "name": "UCCD2063 AI",
                "url": "https://wble-a.utar.edu.my/course/view.php?id=10",
                "target_id": target["id"],
                "target_label": target["label"],
                "folder_name": "UCCD2063 AI",
            }],
        )


class FakePage:
    def __init__(self):
        self.url = ""
        self.visited = []

    async def goto(self, _url, **_kwargs):
        self.url = _url
        self.visited.append(_url)
        return None

    async def wait_for_timeout(self, _milliseconds):
        return None


class ProcessingScanner(WBLEScanner):
    def __init__(self):
        super().__init__()
        self.page = FakePage()

    async def is_authenticated_wble_page(self, _page):
        return True


class LoginProbeScanner(WBLEScanner):
    def __init__(self):
        super().__init__()
        self.page = FakePage()

    async def is_authenticated_wble_page(self, page):
        return "wble-b" in page.url


class AllLoginExpiredScanner(WBLEScanner):
    def __init__(self):
        super().__init__()
        self.page = FakePage()

    async def is_authenticated_wble_page(self, _page):
        return False


class ConcurrencyScanner(WBLEScanner):
    def __init__(self):
        super().__init__()
        self.active = 0
        self.max_active = 0

    async def scan_target_in_isolated_context(self, _target, _state_db):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.02)
            return [], []
        finally:
            self.active -= 1


class FakeStorageContext:
    def __init__(self, state):
        self.state = state

    async def storage_state(self):
        return self.state


class FakeIsolatedContext(FakeStorageContext):
    def __init__(self, state):
        super().__init__(state)
        self.page = FakePage()
        self.closed = False

    async def new_page(self):
        return self.page

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, refreshed_state):
        self.refreshed_state = refreshed_state
        self.received_state = None
        self.context = None

    async def new_context(self, *, storage_state, **_kwargs):
        self.received_state = storage_state
        self.context = FakeIsolatedContext(self.refreshed_state)
        return self.context


class FakeInteractiveContext:
    def __init__(self, page):
        self.pages = [page]


class MultiTargetPureTests(unittest.TestCase):
    def test_fresh_install_does_not_pre_register_unused_portals(self):
        original_config = config_mgr.config
        try:
            config_mgr.config = {
                **original_config,
                "dashboard_targets": [],
                "dashboard_targets_version": 0,
                "dashboard_url": "",
            }
            self.assertEqual(get_dashboard_targets(), [])
            self.assertEqual(
                config_mgr.get("dashboard_targets_version"),
                TARGETS_VERSION,
            )
        finally:
            config_mgr.config = original_config

    def test_known_kampar_portals_have_distinct_faculty_labels(self):
        ewble = "https://ewble-kpr.utar.edu.my/"
        wble = "https://wble-kpr.utar.edu.my/wble-kpr/"

        self.assertEqual(
            dashboard_target_label(ewble, "WBLE"),
            "eWBLE-KPR — FAS / FEd / THP / FBF",
        )
        self.assertEqual(
            dashboard_target_label(wble, "WBLE"),
            "WBLE-KPR — FEGT / FICT / FSc / FCS",
        )
        self.assertNotEqual(
            dashboard_target_id(ewble),
            dashboard_target_id(wble),
        )
        self.assertEqual(
            canonical_dashboard_target_url(
                "https://ewble-kpr.utar.edu.my/my/?redirect=0"
            ),
            ewble,
        )
        self.assertEqual(
            canonical_dashboard_target_url(
                "https://wble-kpr.utar.edu.my/wble-kpr/my/"
            ),
            wble,
        )

        targets = normalize_dashboard_targets([
            {"url": ewble, "label": "Old generic label"},
            {"url": wble, "label": "Old generic label"},
        ])
        self.assertEqual(len(targets), 2)
        self.assertEqual(
            {target["label"] for target in targets},
            {
                "eWBLE-KPR — FAS / FEd / THP / FBF",
                "WBLE-KPR — FEGT / FICT / FSc / FCS",
            },
        )

    def test_version_two_migration_preserves_only_registered_routes(self):
        original_config = config_mgr.config
        previous_dir = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            try:
                config_mgr.config = {
                    **original_config,
                    "dashboard_targets": [{
                        "url": (
                            "https://wble-kpr.utar.edu.my/"
                            "wble-kpr/my/"
                        ),
                    }],
                    "dashboard_targets_version": 1,
                    "dashboard_url": "",
                }
                targets = get_dashboard_targets()
                self.assertEqual(len(targets), 1)
                self.assertEqual(
                    targets[0]["url"],
                    "https://wble-kpr.utar.edu.my/wble-kpr/",
                )
            finally:
                config_mgr.config = original_config
                os.chdir(previous_dir)

    def test_legacy_target_migration_and_deduplication(self):
        legacy = "https://wble-kpr.utar.edu.my/wble-kpr/"
        targets = normalize_dashboard_targets(
            [
                {"url": legacy, "label": "Faculty A"},
                {"url": legacy + "#duplicate"},
                {"url": "https://example.com/not-wble"},
            ],
            legacy,
        )
        self.assertEqual(len(targets), 1)
        self.assertEqual(
            targets[0]["label"],
            "WBLE-KPR — FEGT / FICT / FSc / FCS",
        )
        self.assertEqual(targets[0]["id"], dashboard_target_id(legacy))

    def test_same_course_name_uses_distinct_url_keys_and_folders(self):
        first_key = course_state_key(
            "https://wble-a.utar.edu.my/course/view.php?id=1"
        )
        second_key = course_state_key(
            "https://wble-b.utar.edu.my/course/view.php?id=1"
        )
        self.assertNotEqual(first_key, second_key)
        state = {
            first_key: {
                "course_name": "UCCD2063 AI",
                "folder_name": "UCCD2063 AI",
            }
        }
        second_folder = unique_course_folder_name(
            "UCCD2063 AI", second_key, state
        )
        self.assertTrue(second_folder.startswith("UCCD2063 AI ["))
        self.assertNotEqual(second_folder, "UCCD2063 AI")

    def test_removed_target_is_not_revived_by_legacy_url(self):
        original_config = config_mgr.config
        try:
            config_mgr.config = {
                **original_config,
                "dashboard_targets": [],
                "dashboard_targets_version": TARGETS_VERSION,
                "dashboard_url": (
                    "https://wble-old.utar.edu.my/dashboard"
                ),
            }
            self.assertEqual(get_dashboard_targets(), [])
        finally:
            config_mgr.config = original_config

    def test_force_scan_registration_adds_instead_of_replacing(self):
        original_config = config_mgr.config
        previous_dir = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            try:
                config_mgr.config = {
                    **original_config,
                    "dashboard_targets": [],
                    "dashboard_targets_version": TARGETS_VERSION,
                    "dashboard_url": "",
                }
                first = register_dashboard_target(
                    "https://wble-a.utar.edu.my/dashboard",
                    "Faculty A",
                )
                second = register_dashboard_target(
                    "https://wble-b.utar.edu.my/dashboard",
                    "Faculty B",
                )
                register_dashboard_target(
                    first["url"], "Faculty A"
                )
                targets = get_dashboard_targets()
                self.assertEqual(len(targets), 2)
                self.assertEqual(
                    {target["id"] for target in targets},
                    {first["id"], second["id"]},
                )
            finally:
                config_mgr.config = original_config
                os.chdir(previous_dir)

    def test_targets_use_distinct_auth_state_files(self):
        first = {
            "url": "https://wble-a.utar.edu.my/dashboard",
        }
        second = {
            "url": "https://wble-b.utar.edu.my/dashboard",
        }
        self.assertNotEqual(
            target_auth_state_path(first),
            target_auth_state_path(second),
        )


class MultiTargetOrchestrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_force_scan_stops_when_user_closes_browser(self):
        scanner = WBLEScanner()
        scanner.page = FakePage()
        scanner.context = FakeInteractiveContext(scanner.page)
        scanner.browser_closed_event = asyncio.Event()

        login_wait = asyncio.create_task(
            scanner.wait_for_login(is_background=False)
        )
        await asyncio.sleep(0)
        scanner.browser_closed_event.set()

        self.assertFalse(await asyncio.wait_for(login_wait, timeout=0.5))

    async def test_target_auth_state_round_trip(self):
        previous_dir = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            try:
                target = {
                    "url": "https://wble-a.utar.edu.my/dashboard",
                }
                state = {
                    "cookies": [{
                        "name": "session",
                        "value": "faculty-a",
                        "domain": "wble-a.utar.edu.my",
                        "path": "/",
                    }],
                    "origins": [],
                }
                await save_target_auth_state(
                    FakeStorageContext(state), target
                )
                self.assertEqual(load_target_auth_state(target), state)
            finally:
                os.chdir(previous_dir)

    async def test_isolated_scan_loads_and_refreshes_only_its_target(self):
        previous_dir = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            try:
                target = {
                    "url": "https://wble-a.utar.edu.my/dashboard",
                    "label": "Faculty A",
                }
                initial_state = {
                    "cookies": [{"name": "session", "value": "old"}],
                    "origins": [],
                }
                refreshed_state = {
                    "cookies": [{"name": "session", "value": "new"}],
                    "origins": [],
                }
                await save_target_auth_state(
                    FakeStorageContext(initial_state), target
                )
                scanner = WBLEScanner()
                scanner.browser = FakeBrowser(refreshed_state)
                with patch.object(
                    scanner,
                    "scan_dashboard_target",
                    new=AsyncMock(return_value=([], [])),
                ) as scan:
                    self.assertEqual(
                        await scanner.scan_target_in_isolated_context(
                            target, {}
                        ),
                        ([], []),
                    )

                self.assertEqual(
                    scanner.browser.received_state, initial_state
                )
                self.assertIs(
                    scan.await_args.kwargs["page"],
                    scanner.browser.context.page,
                )
                self.assertTrue(scanner.browser.context.closed)
                self.assertEqual(
                    load_target_auth_state(target), refreshed_state
                )
            finally:
                os.chdir(previous_dir)

    async def test_all_expired_targets_are_marked_for_login(self):
        original_config = config_mgr.config
        previous_dir = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            try:
                targets = normalize_dashboard_targets([
                    {
                        "url": "https://wble-a.utar.edu.my/dashboard",
                        "label": "Faculty A",
                    },
                    {
                        "url": "https://wble-b.utar.edu.my/dashboard",
                        "label": "Faculty B",
                    },
                ])
                config_mgr.config = {
                    **original_config,
                    "dashboard_targets": targets,
                    "dashboard_targets_version": TARGETS_VERSION,
                }
                scanner = AllLoginExpiredScanner()
                with patch(
                    "core.engine.asyncio.sleep",
                    new=AsyncMock(),
                ):
                    self.assertFalse(
                        await scanner.find_background_login()
                    )
                self.assertEqual(
                    scanner.last_scan_report["targets_ok"], 0
                )
                self.assertEqual(
                    {
                        target["last_status"]
                        for target in config_mgr.get(
                            "dashboard_targets", []
                        )
                    },
                    {"login_required"},
                )
            finally:
                config_mgr.config = original_config
                os.chdir(previous_dir)

    async def test_background_accepts_one_independently_authorized_target(self):
        original_config = config_mgr.config
        try:
            target_a = {
                "id": dashboard_target_id(
                    "https://wble-a.utar.edu.my/dashboard"
                ),
                "url": "https://wble-a.utar.edu.my/dashboard",
                "label": "Faculty A",
                "version": TARGETS_VERSION,
            }
            target_b = {
                "id": dashboard_target_id(
                    "https://wble-b.utar.edu.my/dashboard"
                ),
                "url": "https://wble-b.utar.edu.my/dashboard",
                "label": "Faculty B",
                "version": TARGETS_VERSION,
            }
            config_mgr.config = {
                **original_config,
                "dashboard_targets": [target_a, target_b],
                "dashboard_targets_version": TARGETS_VERSION,
            }
            scanner = LoginProbeScanner()
            with patch(
                "core.engine.load_target_auth_state",
                side_effect=lambda target: (
                    {"cookies": [{"name": "session"}], "origins": []}
                    if target["id"] == target_b["id"]
                    else None
                ),
            ) as load_state:
                self.assertTrue(await scanner.find_background_login())
            self.assertEqual(
                load_state.call_count,
                2,
            )
        finally:
            config_mgr.config = original_config

    async def test_background_concurrency_is_capped_at_two(self):
        original_config = config_mgr.config
        original_state = config_mgr.state
        previous_dir = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            try:
                targets = normalize_dashboard_targets([
                    {
                        "url": f"https://wble-{suffix}.utar.edu.my/dashboard",
                        "label": f"Faculty {suffix.upper()}",
                    }
                    for suffix in ("a", "b", "c")
                ])
                config_mgr.config = {
                    **original_config,
                    "dashboard_targets": targets,
                    "dashboard_targets_version": TARGETS_VERSION,
                    "available_courses": [],
                }
                config_mgr.state = {}
                scanner = ConcurrencyScanner()
                await scanner.run_scan_cycle()
                self.assertEqual(MAX_CONCURRENT_TARGETS, 2)
                self.assertEqual(scanner.max_active, 2)
                self.assertEqual(
                    scanner.last_scan_report["targets_ok"], 3
                )
            finally:
                config_mgr.config = original_config
                config_mgr.state = original_state
                os.chdir(previous_dir)

    async def test_one_failed_target_does_not_stop_the_other(self):
        original_config = config_mgr.config
        original_state = config_mgr.state
        previous_dir = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            try:
                target_a = {
                    "id": dashboard_target_id(
                        "https://wble-a.utar.edu.my/dashboard"
                    ),
                    "url": "https://wble-a.utar.edu.my/dashboard",
                    "label": "Faculty A",
                    "version": TARGETS_VERSION,
                }
                target_b = {
                    "id": dashboard_target_id(
                        "https://wble-b.utar.edu.my/dashboard"
                    ),
                    "url": "https://wble-b.utar.edu.my/dashboard",
                    "label": "Faculty B",
                    "version": TARGETS_VERSION,
                }
                retained_key = course_state_key(
                    "https://wble-b.utar.edu.my/course/view.php?id=20"
                )
                config_mgr.config = {
                    **original_config,
                    "dashboard_targets": [target_a, target_b],
                    "dashboard_targets_version": TARGETS_VERSION,
                    "dashboard_url": target_b["url"],
                    "available_courses": [{
                        "key": retained_key,
                        "name": "MPU34012",
                        "url": (
                            "https://wble-b.utar.edu.my/"
                            "course/view.php?id=20"
                        ),
                        "target_id": target_b["id"],
                        "target_label": target_b["label"],
                        "folder_name": "MPU34012",
                    }],
                }
                config_mgr.state = {}

                scanner = FakeMultiTargetScanner()
                updates = await scanner.run_scan_cycle()

                self.assertEqual(
                    scanner.visited, [target_a["id"], target_b["id"]]
                )
                self.assertEqual(len(updates), 1)
                report = scanner.last_scan_report
                self.assertEqual(report["targets_total"], 2)
                self.assertEqual(report["targets_ok"], 1)
                self.assertEqual(
                    report["targets_failed"][0]["reason"],
                    "login_required",
                )
                available_keys = {
                    item["key"]
                    for item in config_mgr.get("available_courses", [])
                }
                self.assertIn(retained_key, available_keys)
                self.assertIn(updates[0]["course_key"], available_keys)
                statuses = {
                    target["label"]: target["last_status"]
                    for target in config_mgr.get(
                        "dashboard_targets", []
                    )
                }
                self.assertEqual(statuses["Faculty A"], "ok")
                self.assertEqual(
                    statuses["Faculty B"], "login_required"
                )
            finally:
                config_mgr.config = original_config
                config_mgr.state = original_state
                os.chdir(previous_dir)

    async def test_actual_scan_keeps_same_name_courses_separate(self):
        original_config = config_mgr.config
        original_state = config_mgr.state
        previous_dir = os.getcwd()
        with tempfile.TemporaryDirectory() as temp_dir:
            os.chdir(temp_dir)
            try:
                config_mgr.config = {
                    **original_config,
                    "download_dir": temp_dir,
                    "blacklisted_courses": [],
                    "blacklisted_course_keys": [],
                }
                config_mgr.state = {}
                scanner = ProcessingScanner()
                target_a = {
                    "id": dashboard_target_id(
                        "https://wble-a.utar.edu.my/dashboard"
                    ),
                    "url": "https://wble-a.utar.edu.my/dashboard",
                    "label": "Faculty A",
                }
                target_b = {
                    "id": dashboard_target_id(
                        "https://wble-b.utar.edu.my/dashboard"
                    ),
                    "url": "https://wble-b.utar.edu.my/dashboard",
                    "label": "Faculty B",
                }
                snapshot = {
                    "version": 2,
                    "sections": [],
                    "external_links": [],
                }
                deep_result = (0, "", snapshot, True)

                with patch(
                    "core.engine.extract_course_links",
                    new=AsyncMock(return_value={
                        "UCCD2063 AI": (
                            "https://wble-a.utar.edu.my/"
                            "course/view.php?id=1"
                        )
                    }),
                ), patch(
                    "core.engine.deep_scan_course",
                    new=AsyncMock(return_value=deep_result),
                ):
                    await scanner.scan_dashboard_target(
                        target_a, config_mgr.state
                    )

                with patch(
                    "core.engine.extract_course_links",
                    new=AsyncMock(return_value={
                        "UCCD2063 AI": (
                            "https://wble-b.utar.edu.my/"
                            "course/view.php?id=1"
                        )
                    }),
                ), patch(
                    "core.engine.deep_scan_course",
                    new=AsyncMock(return_value=deep_result),
                ):
                    await scanner.scan_dashboard_target(
                        target_b, config_mgr.state
                    )

                course_states = [
                    state
                    for key, state in config_mgr.state.items()
                    if key.startswith("course:")
                ]
                self.assertEqual(len(course_states), 2)
                self.assertEqual(
                    {state["target_label"] for state in course_states},
                    {"Faculty A", "Faculty B"},
                )
                self.assertEqual(
                    len({
                        state["folder_name"]
                        for state in course_states
                    }),
                    2,
                )
            finally:
                config_mgr.config = original_config
                config_mgr.state = original_state
                os.chdir(previous_dir)


if __name__ == "__main__":
    unittest.main()
