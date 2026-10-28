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
    def test_type_selectors_validate_roles_and_unique_visible_password_fields(self):
        source = (android.SKILL_DIR / "android/SecureInput.java").read_text()
        selectors = "    private static void keys(" + source.split("    private static void keys(", 1)[1].split("    private static final class Connection {", 1)[0]
        login = "        AccessibilityNodeInfo[] login(" + source.split("        AccessibilityNodeInfo[] login(", 1)[1].split("        String authenticate(", 1)[0]
        success = "        boolean hasSuccessMarker(" + source.split("        boolean hasSuccessMarker(", 1)[1].split("        AccessibilityNodeInfo[] login(", 1)[0]
        preflight = "        String preflight(" + source.split("        String preflight(", 1)[1].split("        void ensureEnvironment(", 1)[0]
        find = "        List<AccessibilityNodeInfo> find(" + source.split("        List<AccessibilityNodeInfo> find(", 1)[1].split("        @Override public void close()", 1)[0]
        self._check_java(r'''
import java.util.*;
import java.nio.charset.StandardCharsets;
public final class LoginObservationCheck {
  private static final String RELEASE_DIGEST = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
  static class JSONObject {
    final Map<String, Object> values;
    JSONObject(Map<String, Object> values) { this.values = new HashMap<>(values); }
    JSONObject(String unused) { throw new IllegalArgumentException(); }
    Iterator<String> keys() { return values.keySet().iterator(); }
    int length() { return values.size(); }
    boolean has(String key) { return values.containsKey(key); }
    boolean isNull(String key) { return values.get(key) == null; }
    Object get(String key) { if (!has(key)) throw new IllegalArgumentException(); return values.get(key); }
    JSONObject getJSONObject(String key) { return (JSONObject) get(key); }
    JSONArray getJSONArray(String key) { return (JSONArray) get(key); }
  }
  static class JSONArray {
    final List<JSONObject> values;
    JSONArray(JSONObject... values) { this.values = List.of(values); }
    int length() { return values.size(); }
    JSONObject getJSONObject(int index) { return values.get(index); }
  }
  static class AccessibilityNodeInfo {
    static final int ACTION_SET_TEXT = 1, ACTION_CLICK = 2;
    boolean visible = true, editable, password, enabled = true, clickable;
    String appId = "org.example.fixture", identifier = "", label = "", hint = "", text = "";
    AccessibilityNodeInfo(boolean editable, boolean password) { this.editable = editable; this.password = password; }
    boolean isVisibleToUser() { return visible; }
    boolean isEditable() { return editable; }
    boolean isPassword() { return password; }
    boolean isEnabled() { return enabled; }
    boolean isClickable() { return clickable; }
    String getViewIdResourceName() { return identifier; }
    String getContentDescription() { return label; }
    String getHintText() { return hint; }
    String getText() { return text; }
    String getPackageName() { return appId; }
  }
  static boolean supports(AccessibilityNodeInfo node, int action) { return true; }
''' + selectors + r'''
  static class Tree implements AutoCloseable {
    static Tree current;
    static Tree capture(Object automation, String appId) { return current; }
    List<AccessibilityNodeInfo> nodes = new ArrayList<>();
    public void close() {}
''' + find + r'''
  }
  static class Session {
    final Plan plan;
    Object automation = new Object();
    void ensureEnvironment(boolean ignored) {}
    Session(Plan plan) { this.plan = plan; }
''' + login + success + preflight + r'''
  }
  static JSONObject selector(String key, String value) { return new JSONObject(Map.of(key, value)); }
  static JSONObject plan() {
    return new JSONObject(Map.of("app_id", "org.example.fixture",
      "username_selector", selector("type", "textField"), "password_selector", selector("type", "secureTextField"),
      "submit_selector", selector("label", "Sign in"), "environment_selector", selector("label", "Staging"),
      "success_selectors", new JSONArray(selector("label", "Home")), "timeout_ms", 1000, "release_digest", RELEASE_DIGEST));
  }
  static void check(boolean condition) { if (!condition) throw new AssertionError(); }
  static void invalid(String key, Object value) throws Exception {
    JSONObject object = plan(); object.values.put(key, value);
    try { new Plan(object); throw new AssertionError("unsafe plan accepted"); }
    catch (Failure failure) { check(failure.code.equals("invalid_plan")); }
  }
  static void rejected(Session session, Tree tree, String code) throws Exception {
    try { session.login(tree, true); throw new AssertionError("ambiguous fields accepted"); }
    catch (Failure failure) { check(failure.code.equals(code)); }
  }
  public static void main(String[] args) throws Exception {
    Plan typed = new Plan(plan());
    invalid("username_selector", selector("type", "secureTextField"));
    invalid("password_selector", selector("type", "textField"));
    invalid("submit_selector", selector("type", "textField"));
    invalid("environment_selector", selector("type", "secureTextField"));
    invalid("success_selectors", new JSONArray(selector("type", "textField")));
    invalid("username_selector", selector("type", "textView"));
    invalid("username_selector", new JSONObject(Map.of("type", "textField", "label", "Account")));
    invalid("username_selector", new JSONObject(Map.of("type", "textField", "index", "0")));
    invalid("username_selector", new JSONObject(Map.of()));
    JSONObject unobserved = plan(); unobserved.values.put("environment_selector", null);
    check(new Plan(unobserved).environment == null);
    JSONObject missing = plan(); missing.values.remove("environment_selector");
    try { new Plan(missing); throw new AssertionError("missing environment selector accepted"); }
    catch (Failure failure) { check(failure.code.equals("invalid_plan")); }
    AccessibilityNodeInfo user = new AccessibilityNodeInfo(true, false), pass = new AccessibilityNodeInfo(true, true);
    AccessibilityNodeInfo button = new AccessibilityNodeInfo(false, false); button.clickable = true; button.label = "Sign in";
    Tree tree = new Tree(); tree.nodes = new ArrayList<>(List.of(user, pass, button));
    Session session = new Session(typed);
    check(session.login(tree, true)[0] == user && session.login(tree, true)[1] == pass);
    user.identifier = "org.example.fixture:id/user"; user.hint = "Account"; user.text = "entered-account";
    check(new Selector(selector("identifier", "org.example.fixture:id/user"), typed.appId).matches(user));
    check(new Selector(selector("label", "Account"), typed.appId).matches(user));
    check(!new Selector(selector("label", "entered-account"), typed.appId).matches(user));
    tree.nodes.add(new AccessibilityNodeInfo(true, false)); rejected(session, tree, "login_elements_not_unique");
    tree.nodes.remove(tree.nodes.size() - 1); tree.nodes.add(new AccessibilityNodeInfo(true, true));
    rejected(session, tree, "login_elements_not_unique"); tree.nodes.remove(tree.nodes.size() - 1);
    pass.visible = false; rejected(session, tree, "login_elements_not_unique"); pass.visible = true;
    pass.password = false; rejected(session, tree, "login_elements_not_unique"); pass.password = true;
    user.enabled = false; rejected(session, tree, "unsafe_login_fields"); user.enabled = true;
    AccessibilityNodeInfo hidden = new AccessibilityNodeInfo(true, false); hidden.visible = false;
    AccessibilityNodeInfo foreign = new AccessibilityNodeInfo(true, false); foreign.appId = "other.app";
    tree.nodes.add(hidden); tree.nodes.add(foreign); check(session.login(tree, true)[0] == user);
    JSONObject legacy = plan(); legacy.values.put("username_selector", selector("identifier", "org.example.fixture:id/user"));
    legacy.values.put("password_selector", selector("label", "Secret")); pass.hint = "Secret";
    check(new Session(new Plan(legacy)).login(tree, true)[1] == pass);
    AccessibilityNodeInfo search = new AccessibilityNodeInfo(true, false), home = new AccessibilityNodeInfo(false, false);
    home.label = "Home"; tree.nodes = new ArrayList<>(List.of(search, home)); Tree.current = tree;
    check(session.isAuthenticated(tree) && session.preflight(false).equals("already_authenticated"));
    tree.nodes.add(button); check(!session.isAuthenticated(tree)); tree.nodes.remove(button);
    tree.nodes.remove(home); check(!session.isAuthenticated(tree)); tree.nodes.add(home);
    AccessibilityNodeInfo duplicate = new AccessibilityNodeInfo(false, false); duplicate.label = "Home";
    tree.nodes.add(duplicate); check(!session.isAuthenticated(tree)); tree.nodes.remove(duplicate);
    Session named = new Session(new Plan(legacy)); check(named.isAuthenticated(tree));
    tree.nodes.add(user); check(!named.isAuthenticated(tree)); tree.nodes.remove(user);
    tree.nodes.add(pass); check(!named.isAuthenticated(tree));
  }
}
''', "typed-selectors")

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
    Selector username = new Selector("username"), password = new Selector("password"), submit = new Selector("submit"), environment = new Selector("environment");
    List<Selector> success = List.of(new Selector("home"));
  }
  static class Selector {
    final String value; String type;
    Selector(String value) { this.value = value; }
  }
  static class Tree implements AutoCloseable {
    static Map<String, List<AccessibilityNodeInfo>> visible = new HashMap<>();
    static Tree capture(Object automation, String appId) { return new Tree(); }
    List<AccessibilityNodeInfo> find(Selector selector, String appId) { return visible.getOrDefault(selector.value, List.of()); }
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
''' + preflight + environment + success + r'''
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
        self._check_java(harness, "login-observations")

    def test_null_environment_selector_reports_unobserved_without_reading_screen(self):
        source = (android.SKILL_DIR / "android/SecureInput.java").read_text()
        environment = "        void ensureEnvironment(" + source.split("        void ensureEnvironment(", 1)[1].split("        boolean hasSuccessMarker(", 1)[0]
        self._check_java(r'''
import java.util.*;
public final class LoginObservationCheck {
  static class Failure extends Exception {
    final String code;
    Failure(String code) { this.code = code; }
  }
  static class AccessibilityNodeInfo {
    boolean selected;
    boolean isEditable() { return false; }
    boolean isPassword() { return false; }
    boolean isSelected() { return selected; }
    boolean isCheckable() { return false; }
    boolean isChecked() { return false; }
  }
  static class Selector {
    final String value;
    Selector(String value) { this.value = value; }
  }
  static class Plan { String appId = "org.example.fixture"; Selector environment; }
  static class Tree implements AutoCloseable {
    static int captures;
    static Map<String, List<AccessibilityNodeInfo>> visible = new HashMap<>();
    static Tree capture(Object automation, String appId) { captures++; return new Tree(); }
    List<AccessibilityNodeInfo> find(Selector selector, String appId) { return visible.getOrDefault(selector.value, List.of()); }
    public void close() {}
  }
  static class Session {
    Object automation = new Object(); Plan plan = new Plan(); boolean environmentVerified = true;
    AccessibilityNodeInfo[] login(Tree tree, boolean requireButton) {
      return new AccessibilityNodeInfo[] {new AccessibilityNodeInfo(), new AccessibilityNodeInfo(), new AccessibilityNodeInfo()};
    }
''' + environment + r'''
  }
  static void check(boolean condition) { if (!condition) throw new AssertionError(); }
  public static void main(String[] args) throws Exception {
    Session session = new Session();
    session.ensureEnvironment(false);
    check(!session.environmentVerified && Tree.captures == 0);
    session.plan.environment = new Selector("environment");
    AccessibilityNodeInfo button = new AccessibilityNodeInfo();
    Tree.visible.put("environment", List.of(button));
    try { session.ensureEnvironment(false); throw new AssertionError("unselected environment accepted"); }
    catch (Failure failure) { check(failure.code.equals("environment_unverifiable")); }
    button.selected = true;
    session.ensureEnvironment(false);
    check(session.environmentVerified && Tree.captures == 2);
  }
}
''', "unobserved-environment")

    def _check_java(self, harness, name):
        directory = android.build_directory().parent / "JVMChecks"
        android.verify_output(directory)
        directory.mkdir(parents=True, exist_ok=True)
        jdk = android.java_home()
        with tempfile.TemporaryDirectory(dir=directory) as folder:
            temporary = Path(folder)
            java = temporary / "LoginObservationCheck.java"
            java.write_text(harness)
            environment = {**os.environ, "TMPDIR": str(temporary) + "/"}
            with (directory / (name + ".log")).open("w") as log:
                compile_result = subprocess.run([str(jdk / "bin/javac"), "-J-Djava.io.tmpdir=" + str(temporary), "-d", str(temporary), str(java)], stdout=log, stderr=log, env=environment)
                self.assertEqual(compile_result.returncode, 0, "합성 로그인 관찰 검사 컴파일 실패")
                run_result = subprocess.run([str(jdk / "bin/java"), "-Djava.io.tmpdir=" + str(temporary), "-cp", str(temporary), "LoginObservationCheck"], stdout=log, stderr=log, env=environment)
                self.assertEqual(run_result.returncode, 0, "합성 로그인 관찰 검사 실패")


if __name__ == "__main__":
    unittest.main()
