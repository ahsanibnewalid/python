import ast
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1] / "UniversityConnect_Production"
V2 = ROOT / "v2_core.py"
WORKSPACE = ROOT / "templates" / "platform_workspace.html"

class PlatformV2StaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = V2.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_v2_imports_and_blueprint(self):
        self.assertIn('Blueprint("v2"', self.source)
        self.assertIn('url_prefix="/platform"', self.source)

    def test_core_domain_tables_exist(self):
        for table in (
            "institutions", "institution_memberships", "programs",
            "academic_sessions", "courses", "course_enrollments",
            "notices", "recorded_classes", "organizations",
            "organization_memberships", "cv_profiles", "jobs",
            "job_applications", "application_events", "conversations",
            "conversation_members", "conversation_messages",
            "platform_notifications",
        ):
            self.assertIn("CREATE TABLE IF NOT EXISTS " + table, self.source)

    def test_major_workflows_exist(self):
        for route in (
            '@bp.post("/institutions/<int:institution_id>/notices")',
            '@bp.post("/courses/<int:course_id>/recordings")',
            '@bp.post("/organizations/<int:organization_id>/jobs")',
            '@bp.post("/jobs/<int:job_id>/apply")',
            '@bp.post("/applications/<int:application_id>/status")',
            '@bp.post("/conversations/<int:conversation_id>/messages")',
            '@bp.get("/notifications")',
        ):
            self.assertIn(route, self.source)

    def test_workspace_exists(self):
        self.assertTrue(WORKSPACE.exists())
        text = WORKSPACE.read_text(encoding="utf-8")
        for marker in ("Jobs", "My CV", "Applications", "Messages", "Notifications", "Manage"):
            self.assertIn(marker, text)

    def test_no_external_social_network_integrations(self):
        forbidden = ("facebook", "whatsapp", "messenger", "tiktok", "wechat")
        lowered = self.source.lower()
        for name in forbidden:
            self.assertNotIn("api." + name, lowered)

if __name__ == "__main__":
    unittest.main()
