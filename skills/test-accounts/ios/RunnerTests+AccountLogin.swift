// Author: JunyoungJung · 2026-10-20
import Foundation
import CryptoKit
import XCTest

private let secureAccountReleaseDigest = "__MOBILE_QA_RELEASE_DIGEST__"

private final class SecureAccountOperation {
  private let lock = NSLock()
  private var cancelled = false
  private var issueCount = 0
  private var inputStarted = false

  var canContinue: Bool {
    lock.lock()
    defer { lock.unlock() }
    return !cancelled && issueCount == 0
  }

  var unconfirmedCleanup: SecureAccountCleanup? {
    lock.lock()
    defer { lock.unlock() }
    return inputStarted ? .unconfirmed : nil
  }

  func beginInput() -> Bool {
    lock.lock()
    defer { lock.unlock() }
    guard !cancelled && issueCount == 0 else { return false }
    inputStarted = true
    return true
  }

  func cancel() {
    lock.lock()
    cancelled = true
    lock.unlock()
  }

  func recordIssue() {
    lock.lock()
    issueCount += 1
    lock.unlock()
  }
}

private enum SecureAccountIssueScope {
  static let lock = NSLock()
  static var active: SecureAccountOperation?

  static func set(_ operation: SecureAccountOperation?) {
    lock.lock()
    active = operation
    lock.unlock()
  }

  static func record() -> Bool {
    lock.lock()
    defer { lock.unlock() }
    guard let active else { return false }
    active.recordIssue()
    return true
  }
}

private struct SecureAccountSelector: Decodable {
  let identifier: String?
  let label: String?
  let placeholder: String?

  var fields: [String: String] {
    ["identifier": identifier, "label": label, "placeholder": placeholder].compactMapValues { $0 }
  }

  var valid: Bool {
    let values = Array(fields.values)
    return values.count == 1 && values.allSatisfy {
      !$0.isEmpty && $0.utf8.count <= 256 && !$0.unicodeScalars.contains { CharacterSet.controlCharacters.contains($0) }
    }
  }
}

private struct SecureAccountPlan: Decodable {
  let app_id: String
  let username_selector: SecureAccountSelector
  let password_selector: SecureAccountSelector
  let submit_selector: SecureAccountSelector
  let success_selectors: [SecureAccountSelector]
  let environment_selector: SecureAccountSelector
  let timeout_ms: Int
  let release_digest: String

  var valid: Bool {
    let selectors = [username_selector, password_selector, submit_selector, environment_selector] + success_selectors
    return app_id.utf8.count <= 200
      && app_id.range(of: #"^[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z][A-Za-z0-9_-]*)+\z"#, options: .regularExpression) != nil
      && release_digest == secureAccountReleaseDigest
      && release_digest.range(of: #"^[a-f0-9]{64}\z"#, options: .regularExpression) != nil
      && (1...8).contains(success_selectors.count) && selectors.allSatisfy(\.valid)
      && (1000...15000).contains(timeout_ms)
      && selectors.enumerated().allSatisfy { index, selector in
        !selectors.prefix(index).contains { $0.fields == selector.fields }
      }
  }
}

private struct SecureAccountRequest: Decodable {
  let secureAccountProtocol: String
  let nonce: String
  let action: String
  let plan: SecureAccountPlan
  let username: String?
  let password: String?

  static func decode(_ data: Data) -> Self? {
    guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
      let action = object["action"] as? String, ["preflight", "login"].contains(action),
      Set(object.keys) == Set(["secureAccountProtocol", "nonce", "action", "plan"] + (action == "login" ? ["username", "password"] : [])),
      let plan = object["plan"] as? [String: Any],
      Set(plan.keys) == Set(["app_id", "username_selector", "password_selector", "submit_selector", "success_selectors", "timeout_ms", "environment_selector", "release_digest"]),
      let success = plan["success_selectors"] as? [[String: String]]
    else { return nil }
    let selectors = ["username_selector", "password_selector", "submit_selector", "environment_selector"]
      .compactMap { plan[$0] as? [String: String] } + success
    guard selectors.allSatisfy({ $0.count == 1 && ["identifier", "label", "placeholder"].contains($0.keys.first ?? "") }),
      let request = try? JSONDecoder().decode(Self.self, from: data), request.plan.valid
    else { return nil }
    return request
  }
}


private enum SecureAccountEnvelope {
  private struct Request: Decodable {
    let secureAccountProtocol: String
    let sealed_request: String
  }

  static func openRequest(_ body: Data, nonce: String) -> Data? {
    guard body.count <= 524288,
      let envelope = try? JSONDecoder().decode(Request.self, from: body),
      envelope.secureAccountProtocol == "agent-test-accounts-v1",
      let combined = Data(base64Encoded: envelope.sealed_request),
      let box = try? AES.GCM.SealedBox(combined: combined)
    else { return nil }
    let key = SymmetricKey(data: SHA256.hash(data: Data(nonce.utf8)))
    guard let clear = try? AES.GCM.open(box, using: key), clear.count <= 262144 else { return nil }
    return clear
  }

  static func sealResponse(_ clear: Data, nonce: String) -> Data? {
    let key = SymmetricKey(data: SHA256.hash(data: Data(nonce.utf8)))
    guard let box = try? AES.GCM.seal(clear, using: key), let combined = box.combined else { return nil }
    return try? JSONSerialization.data(withJSONObject: [
      "protocol": "agent-test-accounts-v1", "sealed_response": combined.base64EncodedString(),
    ], options: [.sortedKeys])
  }
}

private enum SecureAccountCleanup: String {
  case cleared, failed, unavailable, unconfirmed
}

private struct SecureAccountResult {
  let status: String
  let code: String
  let environmentVerified: Bool
  var cleanup: SecureAccountCleanup? = nil

  static func error(_ code: String, cleanup: SecureAccountCleanup? = nil) -> Self {
    Self(status: "error", code: code, environmentVerified: false, cleanup: cleanup)
  }
}

extension RunnerTests {
  // XCTest issues can contain field values and screenshots; this route reports only a fixed failure.
  func recordSecureAccountIssue() -> Bool {
    SecureAccountIssueScope.record()
  }

  func secureAccountUnavailableResponse() -> Data {
    secureAccountResponse(.error("NO_AUTHORIZED_PROTOCOL"))
  }

  func handleSecureAccountRequest(
    _ body: Data,
    completion: @escaping @Sendable ((data: Data, shouldFinish: Bool)) -> Void
  ) {
    guard body.count <= 524288,
      let expectedNonce = ProcessInfo.processInfo.environment["AGENT_TEST_ACCOUNTS_NONCE"],
      UUID(uuidString: expectedNonce) != nil,
      let clearBody = SecureAccountEnvelope.openRequest(body, nonce: expectedNonce),
      let request = SecureAccountRequest.decode(clearBody),
      request.secureAccountProtocol == "agent-test-accounts-v1",
      request.nonce == expectedNonce,
      ["preflight", "login"].contains(request.action), request.plan.valid
    else {
      completion((secureAccountUnavailableResponse(), false))
      return
    }
    let credentialsValid = request.action == "preflight"
      ? request.username == nil && request.password == nil
      : [request.username, request.password].allSatisfy {
        guard let value = $0 else { return false }
        return !value.isEmpty && value.utf8.count <= 16384
      }
    guard credentialsValid else {
      completion((secureAccountResponse(.error("INVALID_CREDENTIALS"), nonce: expectedNonce), false))
      return
    }
    commandExecutionQueue.async {
      guard case .idle = self.currentMainThreadBusyState() else {
        completion((self.secureAccountResponse(.error("RUNNER_BUSY"), nonce: expectedNonce), false))
        return
      }
      let operation = SecureAccountOperation()
      do {
        let result = try self.runMainThreadWork(
          "secure-account",
          timeout: Double(request.plan.timeout_ms) / 1000 + 15,
          timeoutError: { NSError(domain: "SecureAccountInput", code: 1) },
          onAbandoned: { operation.cancel() }
        ) {
          SecureAccountIssueScope.set(operation)
          defer { SecureAccountIssueScope.set(nil) }
          var result = SecureAccountResult.error("UI_FAILURE")
          let exception = RunnerObjCExceptionCatcher.catchException {
            result = self.performSecureAccountRequest(request, operation: operation)
          }
          if exception != nil || !operation.canContinue {
            return SecureAccountResult.error("UI_FAILURE", cleanup: result.cleanup ?? operation.unconfirmedCleanup)
          }
          return result
        }
        completion((self.secureAccountResponse(result, nonce: expectedNonce), false))
      } catch {
        completion((self.secureAccountResponse(.error("UI_FAILURE", cleanup: operation.unconfirmedCleanup), nonce: expectedNonce), false))
      }
    }
  }

  private func secureAccountResponse(_ result: SecureAccountResult, nonce: String? = nil) -> Data {
    var payload: [String: Any] = [
      "protocol": "agent-test-accounts-v1", "status": result.status, "code": result.code,
      "environment_verified": result.environmentVerified,
    ]
    if let cleanup = result.cleanup { payload["cleanup"] = cleanup.rawValue }
    let clearBody = try! JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
    let body: Data
    if let nonce {
      guard let sealed = SecureAccountEnvelope.sealResponse(clearBody, nonce: nonce) else {
        return secureAccountUnavailableResponse()
      }
      body = sealed
    } else {
      body = clearBody
    }
    var response = Data("HTTP/1.1 200 OK\r\nX-Agent-Device-Runner-Auth: required\r\nContent-Type: application/json\r\nContent-Length: \(body.count)\r\nConnection: close\r\n\r\n".utf8)
    response.append(body)
    return response
  }

  private func secureAccountMatches(_ selector: SecureAccountSelector, in app: XCUIApplication) -> [XCUIElement] {
    let key = selector.placeholder != nil ? "placeholderValue" : selector.identifier == nil ? "label" : "identifier"
    let value = selector.placeholder ?? selector.identifier ?? selector.label ?? ""
    return app.descendants(matching: .any).matching(NSPredicate(format: "%K == %@", key, value))
      .allElementsBoundByIndex.filter { $0.exists }
  }

  private func secureAccountHasPostLoginMarker(_ plan: SecureAccountPlan, app: XCUIApplication, requireUnique: Bool = false) -> Bool {
    plan.success_selectors.contains { selector in
      let matches = secureAccountMatches(selector, in: app)
      return requireUnique ? matches.count == 1 && matches[0].isHittable : !matches.isEmpty
    }
  }

  private func secureAccountHasSuccess(_ plan: SecureAccountPlan, app: XCUIApplication) -> Bool {
    return app.state == .runningForeground
      && secureAccountMatches(plan.username_selector, in: app).isEmpty
      && secureAccountMatches(plan.password_selector, in: app).isEmpty
      && secureAccountMatches(plan.submit_selector, in: app).isEmpty
      && secureAccountHasPostLoginMarker(plan, app: app, requireUnique: true)
  }

  private func secureAccountEnvironment(_ plan: SecureAccountPlan, app: XCUIApplication) -> XCUIElement? {
    guard let fields = secureAccountFields(plan, app: app) else { return nil }
    let matches = secureAccountMatches(plan.environment_selector, in: app)
    guard matches.count == 1 else { return nil }
    let target = matches[0]
    let aliasesLoginField = [plan.username_selector, plan.password_selector, plan.submit_selector].contains { selector in
      if let identifier = selector.identifier { return target.identifier == identifier }
      if let label = selector.label { return target.label == label }
      return target.placeholderValue == selector.placeholder
    }
    guard target.elementType == .button, target.isEnabled, target.isHittable, target.isSelected,
      ![fields.0, fields.1, fields.2].contains(where: { $0.isEqual(target) }), !aliasesLoginField
    else { return nil }
    return target
  }

  private func secureAccountFields(_ plan: SecureAccountPlan, app: XCUIApplication, forCleanup: Bool = false) -> (XCUIElement, XCUIElement, XCUIElement)? {
    guard app.state == .runningForeground else { return nil }
    let username = secureAccountMatches(plan.username_selector, in: app)
    let password = secureAccountMatches(plan.password_selector, in: app)
    let submit = secureAccountMatches(plan.submit_selector, in: app)
    guard username.count == 1, password.count == 1, submit.count == 1,
      [.textField, .textView].contains(username[0].elementType),
      password[0].elementType == .secureTextField,
      submit[0].elementType == .button,
      username[0].isEnabled, password[0].isEnabled,
      username[0].isHittable, password[0].isHittable,
      (forCleanup || (submit[0].isEnabled && submit[0].isHittable))
    else { return nil }
    return (username[0], password[0], submit[0])
  }


  private func performSecureAccountRequest(
    _ request: SecureAccountRequest,
    operation: SecureAccountOperation
  ) -> SecureAccountResult {
    let plan = request.plan
    let app = XCUIApplication(bundleIdentifier: plan.app_id)
    guard app.state == .runningForeground, operation.canContinue else { return .error("APP_NOT_FOREGROUND") }
    if secureAccountHasSuccess(plan, app: app) {
      return SecureAccountResult(status: "already_authenticated", code: "SESSION_REUSED", environmentVerified: false)
    }
    guard secureAccountFields(plan, app: app) != nil, !secureAccountHasPostLoginMarker(plan, app: app)
    else { return .error("LOGIN_VIEW_NOT_READY") }
    guard secureAccountEnvironment(plan, app: app) != nil, operation.canContinue
    else { return .error("ENVIRONMENT_NOT_VERIFIED") }
    if request.action == "preflight" {
      return SecureAccountResult(status: "ready", code: "LOGIN_VIEW_READY", environmentVerified: true)
    }
    guard let username = request.username, let password = request.password,
      secureAccountFields(plan, app: app) != nil
    else { return .error("LOGIN_VIEW_NOT_READY") }
    // Synthesized Return/Tab would submit or move focus instead of entering a literal character.
    guard ![username, password].contains(where: { value in
      value.unicodeScalars.contains { CharacterSet.controlCharacters.contains($0) }
    }) else { return .error("INPUT_UNAVAILABLE") }
    guard operation.beginInput() else { return .error("INPUT_UNAVAILABLE") }
    var result: SecureAccountResult = {
      guard operation.canContinue,
        let usernameFields = secureAccountFields(plan, app: app),
        secureAccountReplace(username, in: usernameFields.0, app: app),
        operation.canContinue,
        let passwordFields = secureAccountFields(plan, app: app),
        secureAccountReplace(password, in: passwordFields.1, app: app),
        operation.canContinue,
        let submitFields = secureAccountFields(plan, app: app),
        secureAccountEnvironment(plan, app: app) != nil
      else { return .error("INPUT_UNAVAILABLE") }
      submitFields.2.tap()
      let predicate = NSPredicate { _, _ in
        !operation.canContinue || self.secureAccountHasSuccess(plan, app: app)
      }
      let expectation = XCTNSPredicateExpectation(predicate: predicate, object: nil)
      let waited = XCTWaiter.wait(for: [expectation], timeout: Double(plan.timeout_ms) / 1000)
      let authenticated = waited == .completed && operation.canContinue && secureAccountHasSuccess(plan, app: app)
      return SecureAccountResult(
        status: authenticated ? "authenticated" : "login_failed_or_unverified",
        code: authenticated ? "SUCCESS_OBSERVED" : "SUCCESS_NOT_OBSERVED", environmentVerified: authenticated
      )
    }()
    if result.status != "authenticated" {
      result.cleanup = secureAccountClear(plan, app: app)
    }
    return result
  }

  private func secureAccountClear(_ plan: SecureAccountPlan, app: XCUIApplication) -> SecureAccountCleanup {
    var cleanup = SecureAccountCleanup.unavailable
    let exception = RunnerObjCExceptionCatcher.catchException {
      guard app.state == .runningForeground,
        let remaining = self.secureAccountFields(plan, app: app, forCleanup: true)
      else { return }
      let usernameCleared = self.secureAccountReplace("", in: remaining.0, app: app)
      let passwordCleared = self.secureAccountReplace("", in: remaining.1, app: app)
      cleanup = usernameCleared && passwordCleared ? .cleared : .failed
    }
    return exception == nil ? cleanup : .unconfirmed
  }

  private func secureAccountReplace(_ text: String, in element: XCUIElement, app: XCUIApplication) -> Bool {
    guard app.state == .runningForeground, element.exists, element.isHittable else { return false }
    element.tap()
    let replacement = text.isEmpty ? XCUIKeyboardKey.delete.rawValue : text
    let result = RunnerSynthesizedTextEntry.replaceText(withApplication: app, text: replacement,
      charactersPerSecond: TextEntryTiming.synthesizedCharactersPerSecond)
    guard result.status == .succeeded else { return false }
    let predicate = NSPredicate { _, _ in
      guard element.exists, let value = element.value as? String else { return false }
      if value == text { return true }
      if text.isEmpty { return value == element.placeholderValue }
      return element.elementType == .secureTextField && !text.isEmpty
        && value.count == text.count && value.allSatisfy { $0 == "•" || $0 == "●" }
    }
    let expectation = XCTNSPredicateExpectation(predicate: predicate, object: nil)
    return XCTWaiter.wait(for: [expectation], timeout: 3) == .completed
  }
}
