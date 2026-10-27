"""Simulator 환경 선택의 일회성 준비 계약. Author: JunyoungJung · 2026-10-27."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


NATIVE = sys.platform == "darwin" and shutil.which("xcrun") and os.environ.get("MOBILE_QA_NATIVE_TESTS") == "1"
SOURCE = Path(__file__).resolve().parents[1] / "ios/RunnerTests+AccountLogin.swift"


@unittest.skipUnless(NATIVE, "Opt in to isolated Swift tests with MOBILE_QA_NATIVE_TESTS=1")
class NavigationPreparationTests(unittest.TestCase):
    def test_real_success_observation_keeps_original_conditions_and_reports_fixed_reasons(self):
        self._verify_success_fixture(r'''
let fixture = SuccessFixture()
let plan = makePlan()
check(fixture.failure(plan, app: makeApp(foreground: false)) == "APP_NOT_FOREGROUND")
check(fixture.failure(plan, app: makeApp(username: true)) == "LOGIN_VIEW_STILL_PRESENT")
check(fixture.failure(plan, app: makeApp(password: true)) == "LOGIN_VIEW_STILL_PRESENT")
check(fixture.failure(plan, app: makeApp(submit: true)) == "LOGIN_VIEW_STILL_PRESENT")
check(fixture.failure(plan, app: makeApp(markers: 0)) == "SUCCESS_MARKER_MISSING")
check(fixture.failure(plan, app: makeApp(markers: 2)) == "SUCCESS_MARKER_NOT_UNIQUE_OR_HITTABLE")
check(fixture.failure(plan, app: makeApp(hittable: false)) == "SUCCESS_MARKER_NOT_UNIQUE_OR_HITTABLE")
check(fixture.failure(plan, app: makeApp()) == nil)
let alternative = makeApp(markers: 2)
let alternateMarker = XCUIElement("alternate", .button, label: "Alternate")
alternative.elements.append(alternateMarker)
alternative.tabElements.append(alternateMarker)
check(fixture.failure(makePlan(alternate: true), app: alternative) == nil)
for foreground in [false, true] {
  for username in [false, true] {
    for password in [false, true] {
      for submit in [false, true] {
        for markers in 0...2 {
          for hittable in [false, true] {
            for typedUsername in [false, true] {
              for typedPassword in [false, true] {
                let candidate = makePlan(typedUsername: typedUsername, typedPassword: typedPassword)
                let app = makeApp(foreground: foreground, username: username, password: password,
                                  submit: submit, markers: markers, hittable: hittable)
                check(fixture.success(candidate, app: app) == fixture.originalSuccess(candidate, app: app))
                check((fixture.failure(candidate, app: app) == nil) == fixture.originalSuccess(candidate, app: app))
              }
            }
          }
        }
      }
    }
  }
}
print("success-observation-fixture-ok")
''')

    def test_real_wait_retains_last_failure_and_does_not_promote_success_after_timeout(self):
        self._verify_success_fixture(r'''
let fixture = SuccessFixture()
let plan = makePlan()
XCTWaiter.outcome = .completed
XCTWaiter.afterPoll = {}
check(fixture.wait(plan, app: makeApp()).status == "authenticated")
let lateApp = makeApp(foreground: false)
XCTWaiter.outcome = .timedOut
XCTWaiter.afterPoll = { lateApp.state = .runningForeground }
let late = fixture.wait(plan, app: lateApp)
check(late.status == "login_failed_or_unverified")
check(late.code == "APP_NOT_FOREGROUND" && !late.environmentVerified)
let evolvingApp = makeApp(foreground: false)
XCTWaiter.afterFirstPoll = { evolvingApp.state = .runningForeground; evolvingApp.elements.removeAll(); evolvingApp.tabElements.removeAll() }
XCTWaiter.afterPoll = {}
let last = fixture.wait(plan, app: evolvingApp)
check(last.code == "SUCCESS_MARKER_MISSING" && last.status != "authenticated")
XCTWaiter.afterFirstPoll = {}
XCTWaiter.outcome = .completed
let changedApp = makeApp()
XCTWaiter.afterPoll = { changedApp.state = .runningBackground }
let changed = fixture.wait(plan, app: changedApp)
check(changed.code == "APP_NOT_FOREGROUND" && changed.status != "authenticated")
print("success-wait-fixture-ok")
''')

    def _verify_success_fixture(self, assertions):
        scratch = os.environ.get("TMPDIR")
        if not scratch or not Path(scratch).is_absolute() or not Path(scratch).is_dir():
            self.fail("Native verification requires an existing absolute TMPDIR supplied by the caller")
        source = SOURCE.read_text()
        selector = "private struct SecureAccountSelector: Decodable {" + source.split(
            "private struct SecureAccountSelector: Decodable {", 1)[1].split(
                "\nprivate struct SecureAccountNavigation:", 1)[0]
        matchers = "  private func secureAccountMatches(" + source.split(
            "  private func secureAccountMatches(", 1)[1].split(
                "  private func secureAccountHasSuccess(", 1)[0]
        observation = "  private func secureAccountHasSuccess(" + source.split(
            "  private func secureAccountHasSuccess(", 1)[1].split(
                "  private func secureAccountEnvironmentFailure(", 1)[0]
        wait = source.split("      submitFields.2.tap()\n", 1)[1].split("\n    }()", 1)[0]
        fixture = r'''
private class XCUIElement: NSObject {
  enum ElementType { case any, button, staticText, textField, secureTextField }
  @objc dynamic var label: String
  @objc dynamic var identifier: String
  @objc dynamic var placeholderValue = ""
  let elementType: ElementType
  var exists = true
  var isHittable = true
  init(_ identifier: String, _ type: ElementType, label: String = "") {
    self.identifier = identifier; elementType = type; self.label = label
  }
}
private struct XCUIElementQuery {
  var allElementsBoundByIndex: [XCUIElement]
  var buttons: Self { Self(allElementsBoundByIndex: allElementsBoundByIndex.filter { $0.elementType == .button }) }
  func matching(_ predicate: NSPredicate) -> Self {
    Self(allElementsBoundByIndex: allElementsBoundByIndex.filter { predicate.evaluate(with: $0) })
  }
}
private final class XCUIApplication {
  enum State { case runningForeground, runningBackground }
  var state: State = .runningForeground
  var elements: [XCUIElement] = []
  var tabElements: [XCUIElement] = []
  var tabBars: XCUIElementQuery { XCUIElementQuery(allElementsBoundByIndex: tabElements) }
  func descendants(matching type: XCUIElement.ElementType) -> XCUIElementQuery {
    XCUIElementQuery(allElementsBoundByIndex: elements.filter { type == .any || $0.elementType == type })
  }
}
private struct SecureAccountPlan {
  let username_selector: SecureAccountSelector
  let password_selector: SecureAccountSelector
  let submit_selector: SecureAccountSelector
  let success_selectors: [SecureAccountSelector]
  let timeout_ms = 1000
}
private struct SecureAccountResult {
  let status: String
  let code: String
  let environmentVerified: Bool
}
private struct FixtureOperation { let canContinue = true }
private struct XCTNSPredicateExpectation {
  let predicate: NSPredicate
  init(predicate: NSPredicate, object: Any?) { self.predicate = predicate }
}
private enum XCTWaiter {
  enum Result { case completed, timedOut }
  static var outcome: Result = .completed
  static var afterFirstPoll: () -> Void = {}
  static var afterPoll: () -> Void = {}
  static func wait(for expectations: [XCTNSPredicateExpectation], timeout: Double) -> Result {
    _ = expectations[0].predicate.evaluate(with: nil)
    afterFirstPoll()
    _ = expectations[0].predicate.evaluate(with: nil)
    afterPoll()
    return outcome
  }
}
private final class SuccessFixture {
__MATCHERS__
__OBSERVATION__
  func failure(_ plan: SecureAccountPlan, app: XCUIApplication) -> String? {
    secureAccountSuccessFailure(plan, app: app)
  }
  func success(_ plan: SecureAccountPlan, app: XCUIApplication) -> Bool {
    secureAccountHasSuccess(plan, app: app)
  }
  // The predicate before diagnostic extraction is the independent compatibility contract.
  func originalSuccess(_ plan: SecureAccountPlan, app: XCUIApplication) -> Bool {
    return app.state == .runningForeground
      && (plan.username_selector.type != nil || secureAccountMatches(plan.username_selector, in: app).isEmpty)
      && (plan.password_selector.type != nil || secureAccountMatches(plan.password_selector, in: app).isEmpty)
      && secureAccountMatches(plan.submit_selector, in: app).isEmpty
      && secureAccountHasPostLoginMarker(plan, app: app, requireUnique: true)
  }
  func wait(_ plan: SecureAccountPlan, app: XCUIApplication) -> SecureAccountResult {
    let operation = FixtureOperation()
__WAIT__
  }
}
private func decoded(_ json: String) -> SecureAccountSelector {
  try! JSONDecoder().decode(SecureAccountSelector.self, from: Data(json.utf8))
}
private func makePlan(typedUsername: Bool = false, typedPassword: Bool = false, alternate: Bool = false) -> SecureAccountPlan {
  SecureAccountPlan(username_selector: decoded(typedUsername ? #"{"type":"textField"}"# : #"{"identifier":"username"}"#),
    password_selector: decoded(typedPassword ? #"{"type":"secureTextField"}"# : #"{"identifier":"password"}"#),
    submit_selector: decoded(#"{"identifier":"submit"}"#),
    success_selectors: [decoded(#"{"tab_button_label":"Marker"}"#)] + (alternate ? [decoded(#"{"tab_button_label":"Alternate"}"#)] : []))
}
private func makeApp(foreground: Bool = true, username: Bool = false, password: Bool = false,
                     submit: Bool = false, markers: Int = 1, hittable: Bool = true) -> XCUIApplication {
  let app = XCUIApplication()
  app.state = foreground ? .runningForeground : .runningBackground
  if username { app.elements.append(XCUIElement("username", .textField)) }
  if password { app.elements.append(XCUIElement("password", .secureTextField)) }
  if submit { app.elements.append(XCUIElement("submit", .button)) }
  for _ in 0..<markers {
    let marker = XCUIElement("marker", .button, label: "Marker")
    marker.isHittable = hittable; app.elements.append(marker); app.tabElements.append(marker)
  }
  return app
}
private func check(_ value: Bool) { if !value { print("success-observation-fixture-failed"); exit(1) } }
'''
        cache = Path(scratch) / "simulator-navigation-module-cache"
        cache.mkdir(exist_ok=True)
        log_path = Path(scratch) / "success-observation-compile.log"
        with tempfile.TemporaryDirectory(prefix="success-observation-", dir=scratch) as temporary:
            root = Path(temporary)
            swift = root / "main.swift"
            binary = root / "verify"
            swift.write_text("import Foundation\n" + selector + fixture.replace("__MATCHERS__", matchers)
                .replace("__OBSERVATION__", observation).replace("__WAIT__", wait) + "\ndo {\n" + assertions + "\n}\n")
            environment = {**os.environ, "TMPDIR": str(root) + "/", "CLANG_MODULE_CACHE_PATH": str(cache)}
            with log_path.open("w") as log:
                compiled = subprocess.run(["/usr/bin/xcrun", "swiftc", "-module-cache-path", str(cache),
                    str(swift), "-o", str(binary)], env=environment, stdout=log, stderr=log)
                self.assertEqual(compiled.returncode, 0, "Success observation compilation failed; inspect caller TMPDIR log")
                executed = subprocess.run([str(binary)], env=environment, stdout=log, stderr=log)
                self.assertEqual(executed.returncode, 0, "Success observation assertions failed; inspect caller TMPDIR log")

    def test_real_matcher_scopes_success_to_tab_buttons_and_keeps_unique_hittable_requirement(self):
        scratch = os.environ.get("TMPDIR")
        if not scratch or not Path(scratch).is_absolute() or not Path(scratch).is_dir():
            self.fail("Native verification requires an existing absolute TMPDIR supplied by the caller")
        source = SOURCE.read_text()
        selector = "private struct SecureAccountSelector: Decodable {" + source.split(
            "private struct SecureAccountSelector: Decodable {", 1)[1].split(
                "\nprivate struct SecureAccountNavigation:", 1)[0]
        matcher = "  private func secureAccountMatches(" + source.split(
            "  private func secureAccountMatches(", 1)[1].split(
                "  private func secureAccountHasSuccess(", 1)[0]
        fixture = r'''
private class XCUIElement: NSObject {
  enum ElementType { case any, button, staticText, textField, secureTextField }
  @objc dynamic var label: String
  @objc dynamic var identifier = ""
  @objc dynamic var placeholderValue = ""
  var elementType: ElementType
  var exists = true
  var isHittable = true
  init(_ label: String, _ type: ElementType) { self.label = label; elementType = type }
}
private struct XCUIElementQuery {
  var allElementsBoundByIndex: [XCUIElement]
  var buttons: Self { Self(allElementsBoundByIndex: allElementsBoundByIndex.filter { $0.elementType == .button }) }
  func matching(_ predicate: NSPredicate) -> Self {
    Self(allElementsBoundByIndex: allElementsBoundByIndex.filter { predicate.evaluate(with: $0) })
  }
}
private final class XCUIApplication {
  let elements: [XCUIElement]
  let tabBars: XCUIElementQuery
  init(elements: [XCUIElement], tabButtons: [XCUIElement]) {
    self.elements = elements; tabBars = XCUIElementQuery(allElementsBoundByIndex: tabButtons)
  }
  func descendants(matching type: XCUIElement.ElementType) -> XCUIElementQuery {
    XCUIElementQuery(allElementsBoundByIndex: elements.filter { type == .any || $0.elementType == type })
  }
}
private struct SecureAccountPlan { let success_selectors: [SecureAccountSelector] }
private final class MatcherFixture {
__MATCHER__
  func verify() throws {
    let scoped = try JSONDecoder().decode(SecureAccountSelector.self, from: Data(#"{"tab_button_label":"Home"}"#.utf8))
    let generic = try JSONDecoder().decode(SecureAccountSelector.self, from: Data(#"{"label":"Home"}"#.utf8))
    let title = XCUIElement("Home", .staticText)
    let unrelated = XCUIElement("Home", .button)
    let tab = XCUIElement("Home", .button)
    let app = XCUIApplication(elements: [title, unrelated, tab], tabButtons: [tab])
    let plan = SecureAccountPlan(success_selectors: [scoped])
    check(secureAccountMatches(generic, in: app).count == 3)
    check(secureAccountMatches(scoped, in: app).count == 1)
    check(secureAccountMatches(scoped, in: app).first === tab)
    check(secureAccountHasPostLoginMarker(plan, app: app, requireUnique: true))
    tab.isHittable = false
    check(!secureAccountHasPostLoginMarker(plan, app: app, requireUnique: true))
    tab.isHittable = true
    let otherTab = XCUIElement("Home", .button)
    let duplicate = XCUIApplication(elements: [title, tab, otherTab], tabButtons: [tab, otherTab])
    check(!secureAccountHasPostLoginMarker(plan, app: duplicate, requireUnique: true))
    let noTabs = XCUIApplication(elements: [title, unrelated], tabButtons: [])
    check(secureAccountMatches(scoped, in: noTabs).isEmpty)
  }
}
private func check(_ value: Bool) { if !value { print("tab-selector-fixture-failed"); exit(1) } }
try MatcherFixture().verify()
print("tab-selector-fixture-ok")
'''
        cache = Path(scratch) / "simulator-navigation-module-cache"
        cache.mkdir(exist_ok=True)
        log_path = Path(scratch) / "tab-selector-compile.log"
        with tempfile.TemporaryDirectory(prefix="tab-selector-", dir=scratch) as temporary:
            root = Path(temporary)
            swift = root / "main.swift"
            binary = root / "verify"
            swift.write_text("import Foundation\n" + selector + fixture.replace("__MATCHER__", matcher))
            environment = {**os.environ, "TMPDIR": str(root) + "/", "CLANG_MODULE_CACHE_PATH": str(cache)}
            with log_path.open("w") as log:
                compiled = subprocess.run(["/usr/bin/xcrun", "swiftc", "-module-cache-path", str(cache),
                    str(swift), "-o", str(binary)], env=environment, stdout=log, stderr=log)
                self.assertEqual(compiled.returncode, 0, "Tab selector compilation failed; inspect caller TMPDIR log")
                executed = subprocess.run([str(binary)], env=environment, stdout=log, stderr=log)
                self.assertEqual(executed.returncode, 0, "Tab selector assertions failed; inspect caller TMPDIR log")

    def test_real_preparation_binds_the_full_plan_once_and_rejects_consumed_or_stale_state(self):
        scratch = os.environ.get("TMPDIR")
        if not scratch or not Path(scratch).is_absolute() or not Path(scratch).is_dir():
            self.fail("Native verification requires an existing absolute TMPDIR supplied by the caller")
        source = SOURCE.read_text()
        model = "private struct SecureAccountNavigationPreparation {" + source.split(
            "private struct SecureAccountNavigationPreparation {", 1)[1].split(
                "\nprivate final class SecureAccountNavigationSession", 1)[0]
        assertions = r'''
private var state = SecureAccountNavigationPreparation()
assert(!state.markReady("fixture-plan-a", foreground: true, fieldsReady: true))
assert(!state.consume("fixture-plan-a", foreground: true, fieldsReady: true))
assert(state.begin("fixture-plan-a"))
assert(!state.begin("fixture-plan-a"))
assert(!state.begin("fixture-plan-b"))
assert(!state.markReady("fixture-plan-b", foreground: true, fieldsReady: true))
assert(!state.markReady("fixture-plan-a", foreground: false, fieldsReady: true))
assert(!state.markReady("fixture-plan-a", foreground: true, fieldsReady: false))
assert(state.markReady("fixture-plan-a", foreground: true, fieldsReady: true))
assert(state.matches("fixture-plan-a", foreground: true, fieldsReady: true))
assert(!state.consume("fixture-plan-b", foreground: true, fieldsReady: true))
assert(!state.consume("fixture-plan-a", foreground: false, fieldsReady: true))
assert(!state.consume("fixture-plan-a", foreground: true, fieldsReady: false))
assert(state.consume("fixture-plan-a", foreground: true, fieldsReady: true))
assert(!state.consume("fixture-plan-a", foreground: true, fieldsReady: true))
assert(!state.markReady("fixture-plan-a", foreground: true, fieldsReady: true))
assert(!state.matches("fixture-plan-a", foreground: true, fieldsReady: true))
assert(state.matches("fixture-plan-a", foreground: true, fieldsReady: true, afterConsumption: true))
assert(!state.matches("fixture-plan-a", foreground: false, fieldsReady: true, afterConsumption: true))
assert(!state.matches("fixture-plan-b", foreground: true, fieldsReady: true, afterConsumption: true))
print("navigation-preparation-ok")
'''
        cache = Path(scratch) / "simulator-navigation-module-cache"
        cache.mkdir(exist_ok=True)
        log_path = Path(scratch) / "simulator-navigation-compile.log"
        with tempfile.TemporaryDirectory(prefix="simulator-navigation-", dir=scratch) as temporary:
            root = Path(temporary)
            swift = root / "main.swift"
            binary = root / "verify"
            swift.write_text("import Foundation\n" + model + assertions)
            environment = {**os.environ, "TMPDIR": str(root) + "/", "CLANG_MODULE_CACHE_PATH": str(cache)}
            with log_path.open("w") as log:
                compiled = subprocess.run(["/usr/bin/xcrun", "swiftc", "-module-cache-path", str(cache),
                    str(swift), "-o", str(binary)], env=environment, stdout=log, stderr=log)
                self.assertEqual(compiled.returncode, 0, "Navigation preparation compilation failed; inspect caller TMPDIR log")
                executed = subprocess.run([str(binary)], env=environment, stdout=log, stderr=log)
                self.assertEqual(executed.returncode, 0, "Navigation preparation assertions failed; inspect caller TMPDIR log")


class NavigationRunnerContractTests(unittest.TestCase):
    def test_navigation_is_simulator_only_and_checks_runtime_aliases_before_action(self):
        source = SOURCE.read_text()
        self.assertIn("#if !targetEnvironment(simulator)", source)
        self.assertIn("guard environment_navigation == nil else { return false }", source)
        action = source.split("private func secureAccountPrepareNavigation(", 1)[1].split(
            "private func secureAccountChooseNavigationEnvironment", 1)[0]
        self.assertLess(action.index("isEqual(open[0])"), action.index("open[0].tap()"))
        self.assertLess(action.index("isEqual(close[0])"), action.index("close[0].tap()"))
        self.assertIn("[.image, .button].contains(open[0].elementType)", action)
        self.assertIn("press(forDuration: 1.0)", action)
        self.assertIn("secureAccountNavigationClosed(plan, app: app)", action)


if __name__ == "__main__":
    unittest.main()
