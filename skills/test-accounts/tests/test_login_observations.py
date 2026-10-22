"""실제 Android preflight 함수의 합성 JVM 회귀. Author: JunyoungJung · 2026-10-22."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_android_input as android

NATIVE = os.environ.get("MOBILE_QA_NATIVE_TESTS") == "1"


@unittest.skipUnless(NATIVE, "Opt in to isolated JVM tests with MOBILE_QA_NATIVE_TESTS=1")
class LoginObservationTests(unittest.TestCase):
    def test_preflight_rejects_success_already_visible_and_environment_alias(self):
        source = (android.SKILL_DIR / "android/SecureInput.java").read_text()
        preflight = "        String preflight(" + source.split("        String preflight(", 1)[1].split("        void ensureEnvironment(", 1)[0]
        environment = "        AccessibilityNodeInfo environmentNode(" + source.split("        AccessibilityNodeInfo environmentNode(", 1)[1].split("        boolean hasSuccessMarker(", 1)[0]
        success = "        boolean hasSuccessMarker(" + source.split("        boolean hasSuccessMarker(", 1)[1].split("        AccessibilityNodeInfo[] login(", 1)[0]
        harness = r'''
import java.util.*;
public final class LoginObservationCheck {
  static class Failure extends Exception {
    final String code;
    Failure(String code) { this.code = code; }
  }
  static class AccessibilityNodeInfo {
    boolean isEditable() { return false; }
    boolean isPassword() { return false; }
  }
  static class Plan {
    String appId = "org.example.fixture";
    String username = "username", password = "password", submit = "submit", environment = "environment";
    List<String> success = List.of("home");
  }
  static class Selector {}
  static class Tree implements AutoCloseable {
    static Map<String, List<AccessibilityNodeInfo>> visible = new HashMap<>();
    static Tree capture(Object automation, String appId) { return new Tree(); }
    List<AccessibilityNodeInfo> find(String selector, String appId) { return visible.getOrDefault(selector, List.of()); }
    public void close() {}
  }
  static class Session {
    Object automation = new Object(); Plan plan = new Plan(); int environmentChecks;
    AccessibilityNodeInfo[] login(Tree tree, boolean requireButton) throws Failure {
      List<AccessibilityNodeInfo> user = tree.find(plan.username, plan.appId);
      List<AccessibilityNodeInfo> pass = tree.find(plan.password, plan.appId);
      List<AccessibilityNodeInfo> submit = tree.find(plan.submit, plan.appId);
      if (user.size() != 1 || pass.size() != 1 || submit.size() != 1) throw new Failure("login_elements_not_unique");
      return new AccessibilityNodeInfo[] {user.get(0), pass.get(0), submit.get(0)};
    }
    void ensureEnvironment(boolean selection) throws Failure { environmentChecks++; environmentNode(new Tree()); }
''' + preflight + environment + success.replace("for (Selector selector : plan.success)", "for (String selector : plan.success)") + r'''
  }
  static void check(boolean condition) { if (!condition) throw new AssertionError(); }
  static void rejected(Session session, String expected) throws Exception {
    try { session.preflight(false); throw new AssertionError("preflight must reject"); }
    catch (Failure failure) { check(failure.code.equals(expected)); }
  }
  public static void main(String[] args) throws Exception {
    AccessibilityNodeInfo user = new AccessibilityNodeInfo(), pass = new AccessibilityNodeInfo();
    AccessibilityNodeInfo submit = new AccessibilityNodeInfo(), environment = new AccessibilityNodeInfo(), home = new AccessibilityNodeInfo();
    Session session = new Session();
    Tree.visible = new HashMap<>(Map.of("username", List.of(user), "password", List.of(pass), "submit", List.of(submit), "environment", List.of(environment)));
    check(session.preflight(false).equals("ready"));
    session.environmentChecks = 0;
    Tree.visible.put("home", List.of(home));
    rejected(session, "authentication_unconfirmed");
    check(session.environmentChecks == 0);
    Tree.visible.put("home", List.of(home, new AccessibilityNodeInfo()));
    rejected(session, "authentication_unconfirmed");
    Tree.visible = new HashMap<>(Map.of("home", List.of(home)));
    check(session.preflight(false).equals("already_authenticated"));
    Tree.visible = new HashMap<>(Map.of("username", List.of(user), "password", List.of(pass), "submit", List.of(submit), "environment", List.of(submit)));
    rejected(session, "environment_unverifiable");
    Tree.visible.put("environment", List.of(environment));
    check(session.preflight(false).equals("ready"));
  }
}
'''
        directory = android.build_directory().parent / "JVMChecks"
        android.verify_output(directory)
        directory.mkdir(parents=True, exist_ok=True)
        jdk = android.java_home()
        with tempfile.TemporaryDirectory(dir=directory) as folder:
            temporary = Path(folder)
            java = temporary / "LoginObservationCheck.java"
            java.write_text(harness)
            environment = {**os.environ, "TMPDIR": str(temporary) + "/"}
            with (directory / "login-observations.log").open("w") as log:
                compile_result = subprocess.run([str(jdk / "bin/javac"), "-J-Djava.io.tmpdir=" + str(temporary), "-d", str(temporary), str(java)], stdout=log, stderr=log, env=environment)
                self.assertEqual(compile_result.returncode, 0, "합성 로그인 관찰 검사 컴파일 실패")
                run_result = subprocess.run([str(jdk / "bin/java"), "-Djava.io.tmpdir=" + str(temporary), "-cp", str(temporary), "LoginObservationCheck"], stdout=log, stderr=log, env=environment)
                self.assertEqual(run_result.returncode, 0, "합성 로그인 관찰 검사 실패")


if __name__ == "__main__":
    unittest.main()
