import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("upstream_watch", Path(__file__).with_name("upstream_watch.py"))
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class FakeApi:
    def __init__(self, compare_error=False):
        self.compare_error = compare_error
        self.source_refs = []

    def get(self, endpoint):
        if "/compare/" in endpoint:
            if self.compare_error:
                raise RuntimeError("comparison unavailable")
            return {"status": "ahead", "ahead_by": 2, "total_commits": 2,
                    "commits": [{"sha": "c" * 40, "commit": {"message": "feat: useful control\n\nbody"}}],
                    "files": [{"filename": "crates/ui/src/control.rs", "status": "modified"}]}
        if "/releases?" in endpoint:
            return [{"tag_name": "android-nightly", "draft": False, "html_url": "https://github.com/example/android"},
                    {"tag_name": "v1-nightly.1", "draft": True, "html_url": "https://github.com/example/draft"},
                    {"tag_name": "v1-nightly.0", "draft": False, "html_url": "https://github.com/example/release"}]
        if "/commits/main" in endpoint:
            return {"sha": "a" * 40}
        if "/commits/v1-nightly.0" in endpoint:
            return {"sha": "b" * 40}
        raise AssertionError(endpoint)

    def source(self, repository, path, sha):
        self.source_refs.append(sha)
        return "\n".join(f'Rpc.make("method.{i}", {{}})' for i in range(55))


class UpstreamTests(unittest.TestCase):
    def config(self):
        return {"repository": "example/upstream", "branch": "main", "integratedCommit": "d" * 40,
                "releasePattern": r"^v[0-9].*-nightly\."}

    def test_release_selection_ignores_android_and_drafts(self):
        result = watch.observe(FakeApi(), self.config())
        self.assertEqual(result["release"], "v1-nightly.0")
        self.assertEqual(result["targetCommit"], "b" * 40)
        self.assertEqual(result["headCommit"], "a" * 40)
        self.assertEqual(result["commits"][0]["message"], "feat: useful control")
        self.assertTrue(result["commitsMayBeTruncated"])

    def test_unreachable_import_keeps_the_target_and_exposes_comparison_failure(self):
        result = watch.observe(FakeApi(compare_error=True), self.config())
        self.assertEqual(result["targetCommit"], "b" * 40)
        self.assertEqual(result["comparisonError"], "comparison unavailable")

    def test_rpc_parser_reports_unknown_aliases_instead_of_claiming_coverage(self):
        found, unresolved = watch.methods('old: "old.rpc"; next: "next.rpc"; Rpc.make(WS_METHODS.next, {}); Rpc.make(WS_METHODS.changedSyntax, {}); Rpc.make("direct.rpc", {})')
        self.assertEqual(found, {"next.rpc", "direct.rpc"})
        self.assertEqual(unresolved, ["changedSyntax"])

    def test_tree_comparison_detects_new_deleted_changed_and_permission_only_changes(self):
        before = {"same": ("a", "100644"), "old": ("b", "100644"), "changed": ("c", "100644"), "executable": ("d", "100644")}
        after = {"same": ("a", "100644"), "new": ("e", "100644"), "changed": ("f", "100644"), "executable": ("d", "100755")}
        self.assertEqual(watch.tree_difference(before, after), [
            {"path": "changed", "status": "modified"}, {"path": "executable", "status": "modified"},
            {"path": "new", "status": "added"}, {"path": "old", "status": "removed"}])

    def test_native_test_fixtures_do_not_count_as_real_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "native/zeron/crates/t3/src"
            source.mkdir(parents=True)
            (source / "lib.rs").write_text("\n".join(f'client.call("method.{i}", p)' for i in range(12)) + '\n#[cfg(test)] fn test(){client.call("fake.rpc",p);}')
            self.assertNotIn("fake.rpc", watch.native_calls(root))

    def test_contracts_are_loaded_from_the_exact_release_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "native/zeron/crates/t3/src"
            source.mkdir(parents=True)
            (source / "lib.rs").write_text("\n".join(f'client.call("method.{i}", p)' for i in range(12)))
            api = FakeApi()
            result = watch.report(api, root, {"upstreams": {"t3": self.config(), "zeron": self.config()}})
            self.assertEqual(api.source_refs, ["b" * 40, "b" * 40])
            self.assertEqual(result["compatibility"]["nativeCallsMissingFromTarget"], [])
            self.assertEqual(len(result["compatibility"]["candidateUnmappedRpcs"]), 43)
            json.dumps(result)
            self.assertIn("not complete feature parity", watch.markdown(result))


if __name__ == "__main__":
    unittest.main()
