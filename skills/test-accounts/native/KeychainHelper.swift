// Created by JunyoungJung on 2026-10-20.

import AppKit
import Foundation
import Security

@main
@MainActor
struct KeychainHelper {
    private static let service = "com.agent-test-accounts.credentials.v1"

    private enum Failure: Error {
        case invalidArguments
        case cancelled
        case invalidCredentials
        case invalidReference
        case invalidResult

        var code: String {
            switch self {
            case .invalidArguments: "invalid_arguments"
            case .cancelled: "cancelled"
            case .invalidCredentials: "invalid_credentials"
            case .invalidReference: "invalid_reference"
            case .invalidResult: "storage_failed"
            }
        }
    }

    static func main() {
        do {
            try output(run(Array(CommandLine.arguments.dropFirst())))
        } catch {
            if let failure = error as? SecureLoginFailure {
                try? output(["status": "error", "code": failure.code])
                exit(1)
            }
            if let failure = error as? CredentialStoreFailure {
                try? output(["status": "error", "code": failure.code])
                exit(1)
            }
            let failure = error as? Failure ?? .invalidResult
            if case .cancelled = failure {
                try? output(["status": "cancelled"])
                exit(0)
            }
            try? output(["status": "error", "code": failure.code])
            exit(1)
        }
    }

    private static func run(_ arguments: [String]) throws -> [String: Any] {
        if arguments == ["self-test"] || arguments == ["self-test", "--hold"] {
            try SecureLogin.safetySelfTest()
            try CredentialFileStoreSelfTest.run()
            return ["status": "passed"]
        }
        guard let command = arguments.first else { throw Failure.invalidArguments }
        switch command {
        case "login":
            guard arguments.count == 1 else { throw Failure.invalidArguments }
            let request = try SecureLoginRequest.read()
            return try SecureLogin.run(request.plan) {
                do {
                    return try JSONEncoder().encode(CredentialFileStore().read(id: request.id, reference: request.reference))
                } catch let failure as CredentialStoreFailure {
                    throw SecureLoginFailure(code: failure.code)
                }
            }
        case "create":
            let options = try parse(arguments, keys: ["--id", "--context"])
            guard let id = options["--id"], validID(id),
                  let context = options["--context"], validContext(context) else {
                throw Failure.invalidArguments
            }
            let credentials = try prompt(context: context)
            return try createTransaction(id: id, credentials: credentials)
        case "update":
            let options = try parse(arguments, keys: ["--id", "--ref", "--context"])
            let reference = try decodeReference(options["--ref"])
            guard let id = options["--id"], validID(id),
                  let context = options["--context"], validContext(context) else {
                throw Failure.invalidArguments
            }
            _ = try CredentialFileStore().read(id: id, reference: reference)
            try CredentialFileStore().update(id: id, reference: reference, credentials: prompt(context: context))
            return ["status": "updated"]
        case "check":
            guard arguments.count == 1 else { throw Failure.invalidArguments }
            let locators = try CredentialLocator.readBatch()
            return ["status": "ready", "count": try CredentialFileStore().check(locators)]
        case "migrate-keychain":
            guard arguments == ["migrate-keychain"] || arguments == ["migrate-keychain", "--allow-interaction"] else {
                throw Failure.invalidArguments
            }
            let locators = try CredentialLocator.readBatch()
            let allowInteraction = arguments.count == 2
            guard SecKeychainSetUserInteractionAllowed(allowInteraction) == errSecSuccess else {
                throw CredentialStoreFailure.storageFailed
            }
            let count = try CredentialFileStore().migrate(locators) {
                try readLegacy($0, allowInteraction: allowInteraction)
            }
            return ["status": "migrated", "count": count]
        default:
            throw Failure.invalidArguments
        }
    }

    private static func parse(_ arguments: [String], keys: Set<String>) throws -> [String: String] {
        guard arguments.count == 1 + keys.count * 2 else { throw Failure.invalidArguments }
        var options: [String: String] = [:]
        for index in stride(from: 1, to: arguments.count, by: 2) {
            let key = arguments[index]
            guard keys.contains(key), options[key] == nil else { throw Failure.invalidArguments }
            options[key] = arguments[index + 1]
        }
        return options
    }

    private static func validID(_ id: String) -> Bool {
        CredentialLocator.validID(id)
    }

    private static func validContext(_ context: String) -> Bool {
        !context.isEmpty && context.utf8.count <= 1024
            && !context.unicodeScalars.contains { CharacterSet.controlCharacters.contains($0) }
    }

    private static func decodeReference(_ value: String?) throws -> Data {
        guard let value, value.utf8.count <= 8192,
              let reference = Data(base64Encoded: value), !reference.isEmpty,
              reference.base64EncodedString() == value else { throw Failure.invalidReference }
        return reference
    }

    private static func prompt(context: String) throws -> StoredCredentials {
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        app.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "테스트 계정 보관"
        alert.informativeText = "\(context)\n아이디와 비밀번호를 사용자 로컬 credentials.json에 평문으로 저장합니다. 폴더는 0700·파일은 0600으로 제한하며, 같은 macOS 사용자 권한의 프로그램은 읽을 수 있습니다."
        alert.addButton(withTitle: "저장")
        alert.addButton(withTitle: "취소")

        let username = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 320, height: 24))
        username.placeholderString = "아이디"
        username.setAccessibilityLabel("테스트 계정 아이디")
        username.widthAnchor.constraint(equalToConstant: 320).isActive = true
        let password = NSSecureTextField(frame: NSRect(x: 0, y: 0, width: 320, height: 24))
        password.placeholderString = "비밀번호"
        password.setAccessibilityLabel("테스트 계정 비밀번호")
        password.widthAnchor.constraint(equalToConstant: 320).isActive = true
        username.nextKeyView = password
        let fields = NSStackView(views: [username, password])
        fields.orientation = .vertical
        fields.alignment = .leading
        fields.spacing = 12
        fields.frame = NSRect(x: 0, y: 0, width: 320, height: 60)
        alert.accessoryView = fields
        alert.window.initialFirstResponder = username
        defer {
            username.stringValue = ""
            password.stringValue = ""
        }
        guard alert.runModal() == .alertFirstButtonReturn else { throw Failure.cancelled }
        let credentials = StoredCredentials(username: username.stringValue, password: password.stringValue)
        guard !credentials.username.isEmpty, !credentials.password.isEmpty,
              credentials.username.utf8.count <= 16384, credentials.password.utf8.count <= 16384 else {
            throw Failure.invalidCredentials
        }
        return credentials
    }

    private static func createTransaction(id: String, credentials: StoredCredentials) throws -> [String: Any] {
        let store = CredentialFileStore()
        let reference = try store.create(id: id, credentials: credentials)
        do {
            try output(["status": "created", "persistent_ref": reference.base64EncodedString()])
            if readLine() == "commit" { return ["status": "committed"] }
        } catch {
            try store.removeCreated(id: id, reference: reference)
            throw error
        }
        try store.removeCreated(id: id, reference: reference)
        return ["status": "rolled_back"]
    }

    private static func readLegacy(_ locator: CredentialLocator, allowInteraction: Bool) throws -> StoredCredentials {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecMatchItemList as String: [locator.reference],
            kSecAttrService as String: service,
            kSecAttrAccount as String: locator.id,
            kSecAttrSynchronizable as String: false,
            kSecUseDataProtectionKeychain as String: false,
            kSecUseAuthenticationUI as String: allowInteraction ? kSecUseAuthenticationUIAllow : kSecUseAuthenticationUIFail,
            kSecMatchLimit as String: kSecMatchLimitOne,
            kSecReturnAttributes as String: true,
            kSecReturnData as String: true
        ]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        if [errSecInteractionNotAllowed, errSecAuthFailed, errSecUserCanceled].contains(status) {
            throw CredentialStoreFailure.migrationRequiresApproval
        }
        if status == errSecItemNotFound { throw CredentialStoreFailure.invalidCredentials }
        guard status == errSecSuccess else { throw CredentialStoreFailure.storageFailed }
        guard let attributes = result as? [String: Any],
              attributes[kSecAttrService as String] as? String == service,
              attributes[kSecAttrAccount as String] as? String == locator.id,
              let data = attributes[kSecValueData as String] as? Data, data.count <= 262144 else {
            throw CredentialStoreFailure.invalidCredentials
        }
        return try StoredCredentials.decode(data)
    }

    private static func output(_ result: [String: Any]) throws {
        var data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
        data.append(0x0A)
        try FileHandle.standardOutput.write(contentsOf: data)
    }
}
