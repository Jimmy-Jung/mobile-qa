// Created by JunyoungJung on 2026-10-22.

import Darwin
import Foundation
import os

enum CredentialFileStoreSelfTest {
    static func run() throws {
        try SecureLogin.verifyStorage()
        let parent = URL(fileURLWithPath: DriverPins.workRoot, isDirectory: true)
        let root = parent.appendingPathComponent("credential-fixture-" + UUID().uuidString, isDirectory: true)
        guard parent.path == parent.resolvingSymlinksInPath().path,
              root.path == root.resolvingSymlinksInPath().path,
              root.deletingLastPathComponent().path == parent.path else {
            throw CredentialStoreFailure.storageFailed
        }
        guard mkdir(root.path, 0o700) == 0 else { throw CredentialStoreFailure.storageFailed }
        defer { try? FileManager.default.removeItem(at: root) }
        let store = CredentialFileStore(testDirectory: root.path)
        let original = StoredCredentials(username: "합성_\"\\\n", password: "합성_비밀번호_\"\\\n")
        let updated = StoredCredentials(username: original.username, password: "갱신_비밀번호")
        let reference = try store.create(id: "synthetic", credentials: original)
        try require(store.read(id: "synthetic", reference: reference) == original)
        try rejects(.invalidCredentials) { _ = try store.read(id: "wrong", reference: reference) }
        try rejects(.invalidCredentials) { _ = try store.read(id: "synthetic", reference: Data([1])) }
        try rejects(.storageFailed) { _ = try store.create(id: "synthetic", credentials: updated) }
        try require(store.read(id: "synthetic", reference: reference) == original)
        try store.update(id: "synthetic", reference: reference, credentials: updated)
        try require(store.read(id: "synthetic", reference: reference) == updated)
        try store.removeCreated(id: "synthetic", reference: Data([2]))
        try require(store.read(id: "synthetic", reference: reference) == updated)
        let locator = CredentialLocator(id: "synthetic", reference: reference)
        try require(store.check([locator]) == 1)
        try require(store.migrate([locator]) { _ in throw CredentialStoreFailure.selfTestFailed } == 1)
        try rejects(.storageFailed) {
            _ = try store.migrate([CredentialLocator(id: "synthetic", reference: Data([3]))]) { _ in original }
        }
        try rejects(.storageFailed) {
            _ = try store.migrate([CredentialLocator(id: "new", reference: Data([4])),
                                   CredentialLocator(id: "fail", reference: Data([5]))]) { locator in
                if locator.id == "fail" { throw CredentialStoreFailure.storageFailed }
                return original
            }
        }
        try rejects(.invalidCredentials) { _ = try store.read(id: "new", reference: Data([4])) }
        let migrated = CredentialLocator(id: "migrated", reference: Data([6]))
        try require(store.migrate([migrated]) { _ in original } == 1)
        try require(store.read(id: migrated.id, reference: migrated.reference) == original)
        try rejects(.invalidCredentials) { _ = try store.check([locator]) }
        let failures = OSAllocatedUnfairLock(initialState: 0)
        DispatchQueue.concurrentPerform(iterations: 12) { index in
            do { _ = try store.create(id: "parallel-\(index)", credentials: original) }
            catch { failures.withLock { $0 += 1 } }
        }
        try require(failures.withLock { $0 } == 0)
        let file = root.appendingPathComponent("credentials.json")
        let good = try Data(contentsOf: file)
        let object = try JSONSerialization.jsonObject(with: good) as? [String: Any]
        guard let accounts = object?["accounts"] as? [[String: Any]], accounts.count == 14 else {
            throw CredentialStoreFailure.selfTestFailed
        }
        let allLocators = try accounts.map { account in
            guard let id = account["id"] as? String else { throw CredentialStoreFailure.selfTestFailed }
            return CredentialLocator(id: id, reference: try CredentialLocator.reference(account["reference"]))
        }
        try require(store.check(allLocators) == 14)
        for target in [root, file] {
            guard chmod(target.path, target == root ? 0o750 : 0o640) == 0 else {
                throw CredentialStoreFailure.selfTestFailed
            }
            try rejects(.storageFailed) { _ = try store.read(id: "synthetic", reference: reference) }
            guard chmod(target.path, target == root ? 0o700 : 0o600) == 0 else {
                throw CredentialStoreFailure.selfTestFailed
            }
        }
        let corrupt = Data("{\"version\":1,\"accounts\":[] ,\"extra\":true}".utf8)
        try corrupt.write(to: file)
        try rejects(.storageFailed) { _ = try store.create(id: "unsafe", credentials: original) }
        try require(Data(contentsOf: file) == corrupt)
        try Data("{\"version\":1,\"version\":1,\"accounts\":[]}".utf8).write(to: file)
        try rejects(.storageFailed) { _ = try store.create(id: "unsafe", credentials: original) }
        try good.write(to: file)
        let linked = root.appendingPathComponent("fixture-hardlink")
        guard link(file.path, linked.path) == 0 else { throw CredentialStoreFailure.selfTestFailed }
        try rejects(.storageFailed) { _ = try store.read(id: "synthetic", reference: reference) }
        try FileManager.default.removeItem(at: linked)
        let backing = root.appendingPathComponent("fixture-backing")
        try FileManager.default.moveItem(at: file, to: backing)
        guard symlink(backing.path, file.path) == 0 else { throw CredentialStoreFailure.selfTestFailed }
        try rejects(.storageFailed) { _ = try store.read(id: "synthetic", reference: reference) }
        try FileManager.default.removeItem(at: file)
        try FileManager.default.moveItem(at: backing, to: file)
        try store.removeCreated(id: "synthetic", reference: reference)
        try rejects(.invalidCredentials) { _ = try store.read(id: "synthetic", reference: reference) }
        try require(store.check(allLocators.filter { $0.id != "synthetic" }) == 13)
        try rejects(.invalidCredentials) { _ = try store.check([]) }
        try FileManager.default.removeItem(at: root)
    }

    private static func require(_ condition: @autoclosure () throws -> Bool) throws {
        guard try condition() else { throw CredentialStoreFailure.selfTestFailed }
    }

    private static func rejects(_ expected: CredentialStoreFailure, _ operation: () throws -> Void) throws {
        do { try operation() } catch let failure as CredentialStoreFailure where failure == expected { return }
        throw CredentialStoreFailure.selfTestFailed
    }
}
