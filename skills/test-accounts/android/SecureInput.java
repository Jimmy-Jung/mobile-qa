// Author: JunyoungJung · 2026-10-20
package com.agenttestaccounts;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.UiAutomation;
import android.os.Build;
import android.os.Bundle;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.SystemClock;
import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.PrintStream;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.nio.ByteBuffer;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Set;

/** Shell UID 전용 입력 도구. 비밀값은 ready 이후 표준 입력에서만 받는다. */
public final class SecureInput {
    private static final String RELEASE_DIGEST = "__MOBILE_QA_RELEASE_DIGEST__";
    private static final int MAX_NODES = 2048;
    private static final int MAX_DEPTH = 64;
    private static final int MAX_LINE_BYTES = 16384;
    private static final int MAX_CREDENTIAL_BYTES = 262144;
    private static final PrintStream PROTOCOL = System.out;
    private static final InputStream INPUT = new BufferedInputStream(System.in);

    private SecureInput() {}

    public static void main(String[] arguments) {
        // Framework 진단도 비밀 입력과 같은 프로세스의 출력으로 흘러가지 않게 한다.
        PrintStream discarded = new PrintStream(new OutputStream() {
            @Override public void write(int value) {}
            @Override public void write(byte[] bytes, int offset, int length) {}
        });
        System.setOut(discarded);
        System.setErr(discarded);
        Connection connection = null;
        Session session = null;
        String status = "error";
        String code = "internal_error";
        String cleanup = null;
        boolean probe = false;
        JSONObject probeInfo = null;
        int exitCode = 1;
        try {
            if (arguments.length == 1 && "--self-check".equals(arguments[0])) {
                selfCheck();
                status = "self_check_passed";
                code = null;
                exitCode = 0;
            } else {
                probe = arguments.length == 1 && "--probe".equals(arguments[0]);
                boolean preflight = arguments.length == 1 && "--preflight".equals(arguments[0]);
                if (arguments.length != 0 && !probe && !preflight) {
                    throw new Failure("invalid_arguments");
                }
                if (Build.VERSION.SDK_INT < 26) throw new Failure("unsupported_sdk");
                Plan plan = probe ? null : Plan.parse(readLine(5000, MAX_LINE_BYTES));
                connection = Connection.open();
                if (probe) {
                    status = "probe_ready";
                    probeInfo = inspectProbe(connection.automation);
                } else {
                    session = new Session(connection.automation, plan);
                    status = session.preflight(!preflight);
                    if ("ready".equals(status) && !preflight) {
                        write("ready", null, null, null, true);
                        status = session.authenticate();
                    }
                }
                code = null;
                exitCode = 0;
            }
        } catch (Failure failure) {
            status = "not_ready";
            code = failure.code;
            if (session != null && session.inputStarted) status = "failed";
        } catch (Throwable ignored) {
            // 예외 메시지와 stack trace에는 앱 자료가 섞일 수 있다.
            status = session != null && session.inputStarted ? "uncertain" : "error";
        } finally {
            if (session != null && session.inputStarted && !session.authenticated) {
                cleanup = session.clearLoginFields();
            }
            if (connection != null) connection.close();
        }
        write(status, code, cleanup, probeInfo, session == null ? null : session.environmentVerified);
        System.exit(exitCode);
    }

    private static void write(String status, String code, String cleanup, JSONObject probe, Boolean environment) {
        try {
            JSONObject result = new JSONObject().put("status", status);
            if (code != null) result.put("code", code);
            if (cleanup != null) result.put("cleanup", cleanup);
            if (environment != null) result.put("environment_verified", environment);
            if (probe != null) {
                Iterator<String> keys = probe.keys();
                while (keys.hasNext()) {
                    String key = keys.next();
                    result.put(key, probe.get(key));
                }
            }
            PROTOCOL.println(result.toString());
            PROTOCOL.flush();
        } catch (Throwable ignored) {}
    }

    private static String readLine(long timeoutMs, int maximumBytes) throws Exception {
        long deadline = SystemClock.uptimeMillis() + timeoutMs;
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        while (SystemClock.uptimeMillis() < deadline) {
            if (INPUT.available() == 0) {
                SystemClock.sleep(20);
                continue;
            }
            int value = INPUT.read();
            if (value < 0) throw new Failure("input_closed");
            if (value == '\n') {
                return StandardCharsets.UTF_8.newDecoder()
                        .onMalformedInput(CodingErrorAction.REPORT)
                        .onUnmappableCharacter(CodingErrorAction.REPORT)
                        .decode(ByteBuffer.wrap(bytes.toByteArray())).toString();
            }
            if (bytes.size() >= maximumBytes) throw new Failure("input_too_large");
            bytes.write(value);
        }
        throw new Failure("input_timeout");
    }

    private static void keys(JSONObject object, String... allowed) throws Failure {
        Set<String> expected = new HashSet<>(Arrays.asList(allowed));
        Iterator<String> iterator = object.keys();
        while (iterator.hasNext()) {
            if (!expected.contains(iterator.next())) throw new Failure("invalid_plan");
        }
    }

    private static String text(JSONObject object, String key, int maximum) throws Exception {
        Object value = object.get(key);
        if (!(value instanceof String)) throw new Failure("invalid_plan");
        String string = (String) value;
        if (string.isEmpty() || string.getBytes(StandardCharsets.UTF_8).length > maximum) throw new Failure("invalid_plan");
        for (int index = 0; index < string.length(); index++) {
            if (Character.isISOControl(string.charAt(index)) || Character.getType(string.charAt(index)) == Character.FORMAT) throw new Failure("invalid_plan");
        }
        return string;
    }

    private static final class Failure extends Exception {
        final String code;
        Failure(String code) { this.code = code; }
    }

    private static final class Selector {
        final String identifier;
        final String label;

        Selector(JSONObject object, String appId) throws Exception {
            keys(object, "identifier", "label");
            identifier = object.has("identifier") ? text(object, "identifier", 256) : null;
            label = object.has("label") ? text(object, "label", 256) : null;
            if ((identifier == null) == (label == null)) throw new Failure("invalid_plan");
            if (identifier != null && (!identifier.startsWith(appId + ":id/")
                    || !identifier.substring(appId.length() + 4).matches("[A-Za-z_][A-Za-z0-9_]*"))) {
                throw new Failure("invalid_plan");
            }
        }

        boolean matches(AccessibilityNodeInfo node) {
            if (!node.isVisibleToUser()) return false;
            if (identifier != null && !identifier.equals(node.getViewIdResourceName())) return false;
            if (label == null) return true;
            // Editable의 text는 이미 들어 있는 계정값일 수 있으므로 label로 사용하지 않는다.
            return label.contentEquals(safe(node.getContentDescription()))
                    || label.contentEquals(safe(node.getHintText()))
                    || (!node.isEditable() && label.contentEquals(safe(node.getText())));
        }

        boolean same(Selector other) {
            return java.util.Objects.equals(identifier, other.identifier)
                    && java.util.Objects.equals(label, other.label);
        }
    }

    private static CharSequence safe(CharSequence value) { return value == null ? "" : value; }

    private static final class Plan {
        final String appId;
        final Selector username;
        final Selector password;
        final Selector submit;
        final Selector environment;
        final List<Selector> success;
        final int timeoutMs;

        private Plan(JSONObject object) throws Exception {
            keys(object, "app_id", "username_selector", "password_selector", "submit_selector",
                    "environment_selector", "success_selectors", "timeout_ms", "release_digest");
            if (!RELEASE_DIGEST.matches("[a-f0-9]{64}") || !RELEASE_DIGEST.equals(text(object, "release_digest", 64))) {
                throw new Failure("invalid_plan");
            }
            appId = text(object, "app_id", 200);
            if (!appId.matches("[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z][A-Za-z0-9_]*)+")) {
                throw new Failure("invalid_plan");
            }
            username = new Selector(object.getJSONObject("username_selector"), appId);
            password = new Selector(object.getJSONObject("password_selector"), appId);
            submit = new Selector(object.getJSONObject("submit_selector"), appId);
            JSONObject environmentObject = object.getJSONObject("environment_selector");
            environment = new Selector(environmentObject, appId);
            if (username.same(password) || username.same(submit) || password.same(submit)
                    || environment.same(username) || environment.same(password) || environment.same(submit)) {
                throw new Failure("invalid_plan");
            }
            Object timeout = object.get("timeout_ms");
            if (!(timeout instanceof Integer || timeout instanceof Long)) throw new Failure("invalid_plan");
            long milliseconds = ((Number) timeout).longValue();
            if (milliseconds < 1000 || milliseconds > 15000) throw new Failure("invalid_plan");
            timeoutMs = (int) milliseconds;
            JSONArray array = object.getJSONArray("success_selectors");
            if (array.length() < 1 || array.length() > 8) throw new Failure("invalid_plan");
            success = new ArrayList<>();
            for (int index = 0; index < array.length(); index++) {
                Selector selector = new Selector(array.getJSONObject(index), appId);
                if (selector.same(username) || selector.same(password) || selector.same(submit) || selector.same(environment)) {
                    throw new Failure("invalid_plan");
                }
                for (Selector existing : success) {
                    if (existing.same(selector)) throw new Failure("invalid_plan");
                }
                success.add(selector);
            }
        }

        static Plan parse(String line) throws Failure {
            try { return new Plan(new JSONObject(line)); }
            catch (Exception ignored) { throw new Failure("invalid_plan"); }
        }
    }

    private static final class Connection {
        final HandlerThread thread;
        UiAutomation automation;
        boolean connected;

        Connection() { thread = new HandlerThread("secure-input-accessibility"); }

        static Connection open() throws Failure {
            Connection connection = new Connection();
            connection.thread.start();
            try {
                Class<?> rawClass = Class.forName("android.app.UiAutomationConnection");
                Constructor<?> rawConstructor = rawClass.getDeclaredConstructor();
                rawConstructor.setAccessible(true);
                Object raw = rawConstructor.newInstance();
                Class<?> contract = Class.forName("android.app.IUiAutomationConnection");
                Constructor<UiAutomation> constructor = UiAutomation.class
                        .getDeclaredConstructor(Looper.class, contract);
                constructor.setAccessible(true);
                connection.automation = constructor.newInstance(connection.thread.getLooper(), raw);
                Method connect = UiAutomation.class.getDeclaredMethod("connect", int.class);
                connect.setAccessible(true);
                connect.invoke(connection.automation, UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES);
                connection.connected = true;
                AccessibilityServiceInfo service = connection.automation.getServiceInfo();
                if (service == null) throw new Failure("automation_unavailable");
                service.flags |= AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS;
                connection.automation.setServiceInfo(service);
                return connection;
            } catch (Throwable ignored) {
                connection.close();
                // UiAutomation은 한 클라이언트만 허용한다. 기존 runner를 강제 종료하지 않는다.
                throw new Failure("automation_unavailable");
            }
        }

        void close() {
            if (connected) {
                try {
                    Method disconnect = UiAutomation.class.getDeclaredMethod("disconnect");
                    disconnect.setAccessible(true);
                    disconnect.invoke(automation);
                } catch (Throwable ignored) {}
                connected = false;
            }
            thread.quitSafely();
            try { thread.join(1000); }
            catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
        }
    }

    private static final class NodeDepth {
        final AccessibilityNodeInfo node;
        final int depth;
        NodeDepth(AccessibilityNodeInfo node, int depth) { this.node = node; this.depth = depth; }
    }

    private static final class Tree implements AutoCloseable {
        final List<AccessibilityNodeInfo> nodes = new ArrayList<>();

        static Tree capture(UiAutomation automation, String appId) throws Failure {
            Tree tree = new Tree();
            try {
                AccessibilityNodeInfo root = automation.getRootInActiveWindow();
                if (root == null) throw new Failure("screen_unavailable");
                tree.nodes.add(root);
                if (!appId.contentEquals(safe(root.getPackageName()))) throw new Failure("wrong_app");
                ArrayDeque<NodeDepth> pending = new ArrayDeque<>();
                pending.add(new NodeDepth(root, 0));
                while (!pending.isEmpty()) {
                    NodeDepth item = pending.removeFirst();
                    int count = item.node.getChildCount();
                    if (count > 0 && item.depth >= MAX_DEPTH) throw new Failure("tree_limit");
                    if (count > MAX_NODES - tree.nodes.size()) throw new Failure("tree_limit");
                    for (int index = 0; index < count; index++) {
                        AccessibilityNodeInfo child = item.node.getChild(index);
                        if (child == null) continue;
                        tree.nodes.add(child);
                        if (appId.contentEquals(safe(child.getPackageName()))) {
                            pending.add(new NodeDepth(child, item.depth + 1));
                        }
                    }
                }
                return tree;
            } catch (Failure failure) {
                tree.close();
                throw failure;
            } catch (Throwable ignored) {
                tree.close();
                throw new Failure("screen_unavailable");
            }
        }

        List<AccessibilityNodeInfo> find(Selector selector, String appId) {
            List<AccessibilityNodeInfo> found = new ArrayList<>();
            for (AccessibilityNodeInfo node : nodes) {
                if (appId.contentEquals(safe(node.getPackageName())) && selector.matches(node)) found.add(node);
            }
            return found;
        }

        @Override public void close() {
            for (AccessibilityNodeInfo node : nodes) node.recycle();
            nodes.clear();
        }
    }

    private static final class Session {
        final UiAutomation automation;
        final Plan plan;
        boolean inputStarted;
        boolean authenticated;
        boolean environmentVerified;

        Session(UiAutomation automation, Plan plan) { this.automation = automation; this.plan = plan; }

        String preflight(boolean allowEnvironmentSelection) throws Failure {
            try (Tree tree = Tree.capture(automation, plan.appId)) {
                if (isAuthenticated(tree)) return "already_authenticated";
                login(tree, true);
                if (hasSuccessMarker(tree, false)) throw new Failure("authentication_unconfirmed");
            }
            ensureEnvironment(allowEnvironmentSelection);
            return "ready";
        }

        void ensureEnvironment(boolean allowSelection) throws Failure {
            try (Tree tree = Tree.capture(automation, plan.appId)) {
                AccessibilityNodeInfo target = environmentNode(tree);
                environmentVerified = target.isSelected() || (target.isCheckable() && target.isChecked());
                if (!environmentVerified) throw new Failure("environment_unverifiable");
            }
        }

        AccessibilityNodeInfo environmentNode(Tree tree) throws Failure {
            AccessibilityNodeInfo[] fields = login(tree, true);
            List<AccessibilityNodeInfo> matches = tree.find(plan.environment, plan.appId);
            if (matches.size() != 1) throw new Failure("environment_not_unique");
            AccessibilityNodeInfo target = matches.get(0);
            if (target.isEditable() || target.isPassword() || target.equals(fields[0])
                    || target.equals(fields[1]) || target.equals(fields[2])) {
                throw new Failure("environment_unverifiable");
            }
            return target;
        }

        boolean hasSuccessMarker(Tree tree, boolean requireUnique) {
            for (Selector selector : plan.success) {
                int count = tree.find(selector, plan.appId).size();
                if (requireUnique ? count == 1 : count > 0) return true;
            }
            return false;
        }

        boolean isAuthenticated(Tree tree) {
            return tree.find(plan.username, plan.appId).isEmpty()
                    && tree.find(plan.password, plan.appId).isEmpty()
                    && tree.find(plan.submit, plan.appId).isEmpty()
                    && hasSuccessMarker(tree, true);
        }

        AccessibilityNodeInfo[] login(Tree tree, boolean requireButton) throws Failure {
            List<AccessibilityNodeInfo> username = tree.find(plan.username, plan.appId);
            List<AccessibilityNodeInfo> password = tree.find(plan.password, plan.appId);
            List<AccessibilityNodeInfo> submit = tree.find(plan.submit, plan.appId);
            if (username.size() != 1 || password.size() != 1 || submit.size() != 1) {
                throw new Failure("login_elements_not_unique");
            }
            AccessibilityNodeInfo user = username.get(0);
            AccessibilityNodeInfo pass = password.get(0);
            AccessibilityNodeInfo button = submit.get(0);
            if (user.equals(pass) || user.equals(button) || pass.equals(button)
                    || !user.isEditable() || !user.isEnabled() || user.isPassword()
                    || !pass.isEditable() || !pass.isEnabled() || !pass.isPassword()
                    || !supports(user, AccessibilityNodeInfo.ACTION_SET_TEXT)
                    || !supports(pass, AccessibilityNodeInfo.ACTION_SET_TEXT)) {
                throw new Failure("unsafe_login_fields");
            }
            if (requireButton && (!button.isEnabled() || !button.isClickable()
                    || !supports(button, AccessibilityNodeInfo.ACTION_CLICK))) {
                throw new Failure("unsafe_submit");
            }
            return new AccessibilityNodeInfo[] {user, pass, button};
        }

        String authenticate() throws Exception {
            JSONObject credentials;
            String username;
            String password;
            try {
                // 자격 증명을 기다리되, 입력 직전에 화면과 환경을 다시 검사한다.
                credentials = new JSONObject(readLine(120000, MAX_CREDENTIAL_BYTES));
                keys(credentials, "username", "password");
                username = credentialText(credentials, "username");
                password = credentialText(credentials, "password");
            } catch (Exception ignored) {
                throw new Failure("invalid_credentials_input");
            }
            ensureEnvironment(false);
            try (Tree tree = Tree.capture(automation, plan.appId)) {
                AccessibilityNodeInfo[] fields = login(tree, true);
                inputStarted = true;
                if (!setText(fields[0], username)) throw new Failure("username_input_failed");
            }
            ensureEnvironment(false);
            try (Tree tree = Tree.capture(automation, plan.appId)) {
                AccessibilityNodeInfo[] fields = login(tree, true);
                if (!setText(fields[1], password)) throw new Failure("password_input_failed");
            }
            credentials.remove("username");
            credentials.remove("password");
            username = null;
            password = null;
            ensureEnvironment(false);
            try (Tree tree = Tree.capture(automation, plan.appId)) {
                AccessibilityNodeInfo[] fields = login(tree, true);
                if (!fields[2].performAction(AccessibilityNodeInfo.ACTION_CLICK)) {
                    throw new Failure("submit_failed");
                }
            }
            long deadline = SystemClock.uptimeMillis() + plan.timeoutMs;
            while (SystemClock.uptimeMillis() < deadline) {
                try (Tree tree = Tree.capture(automation, plan.appId)) {
                    if (isAuthenticated(tree)) {
                        authenticated = true;
                        return "authenticated";
                    }
                } catch (Failure failure) {
                    if ("wrong_app".equals(failure.code) || "tree_limit".equals(failure.code)) throw failure;
                }
                SystemClock.sleep(150);
            }
            throw new Failure("authentication_unconfirmed");
        }

        String clearLoginFields() {
            try (Tree tree = Tree.capture(automation, plan.appId)) {
                AccessibilityNodeInfo[] fields = login(tree, false);
                boolean userCleared = tryClear(fields[0]);
                boolean passwordCleared = tryClear(fields[1]);
                if (!userCleared || !passwordCleared) return "failed";
            } catch (Throwable ignored) { return "unavailable"; }
            try (Tree tree = Tree.capture(automation, plan.appId)) {
                AccessibilityNodeInfo[] fields = login(tree, false);
                return safe(fields[0].getText()).length() == 0 && safe(fields[1].getText()).length() == 0
                        ? "cleared" : "failed";
            } catch (Throwable ignored) { return "unconfirmed"; }
        }
    }

    private static boolean supports(AccessibilityNodeInfo node, int action) {
        for (AccessibilityNodeInfo.AccessibilityAction candidate : node.getActionList()) {
            if (candidate.getId() == action) return true;
        }
        return false;
    }

    private static String credentialText(JSONObject object, String key) throws Exception {
        Object value = object.get(key);
        if (!(value instanceof String) || ((String) value).isEmpty() || ((String) value).length() > 16384) {
            throw new Failure("invalid_credentials_input");
        }
        return (String) value;
    }

    private static boolean setText(AccessibilityNodeInfo node, String value) {
        Bundle arguments = new Bundle();
        arguments.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value);
        return node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments);
    }

    private static boolean tryClear(AccessibilityNodeInfo node) {
        try { return setText(node, ""); }
        catch (Throwable ignored) { return false; }
    }

    private static JSONObject inspectProbe(UiAutomation automation) throws Exception {
        JSONObject result = new JSONObject().put("automation_connected", true).put("sdk", Build.VERSION.SDK_INT);
        AccessibilityNodeInfo root = automation.getRootInActiveWindow();
        if (root == null) return result.put("screen_available", false);
        String packageName;
        try { packageName = safe(root.getPackageName()).toString(); }
        finally { root.recycle(); }
        if (!packageName.matches("[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z][A-Za-z0-9_]*)+")) {
            return result.put("screen_available", false);
        }
        int editableCount = 0;
        int passwordCount = 0;
        boolean empty = true;
        try (Tree tree = Tree.capture(automation, packageName)) {
            for (AccessibilityNodeInfo node : tree.nodes) {
                if (!packageName.contentEquals(safe(node.getPackageName()))
                        || !node.isVisibleToUser() || !node.isEditable()) continue;
                editableCount++;
                if (node.isPassword()) passwordCount++;
                empty &= safe(node.getText()).length() == 0;
            }
        }
        return result.put("screen_available", true).put("foreground_app_id", packageName)
                .put("editable_count", editableCount).put("password_editable_count", passwordCount)
                .put("editable_text_empty", empty);
    }

    private static void selfCheck() throws Exception {
        String valid = "{\"app_id\":\"example.app\",\"username_selector\":{\"identifier\":\"example.app:id/user\"},"
                + "\"password_selector\":{\"identifier\":\"example.app:id/password\"},"
                + "\"submit_selector\":{\"label\":\"Sign in\"},\"environment_selector\":{\"label\":\"Staging\"},"
                + "\"success_selectors\":[{\"label\":\"Library\"}],\"timeout_ms\":1000,\"release_digest\":\"" + RELEASE_DIGEST + "\"}";
        Plan.parse(valid);
        for (String invalid : new String[] {
                valid.replace("example.app:id/user", "other.app:id/user"),
                valid.replace("1000", "0"),
                valid.replace("1000", "1000.5"),
                valid.replace("\"label\":\"Staging\"", "\"label\":\"Sign in\""),
                valid.replace("\"label\":\"Library\"", "\"label\":\"Sign in\""),
                valid.replace("\"label\":\"Sign in\"", "\"unexpected\":\"value\""),
                valid.replace("{\"app_id\"", "{\"extra\":true,\"app_id\"")}) {
            boolean rejected = false;
            try { Plan.parse(invalid); }
            catch (Failure expected) { rejected = "invalid_plan".equals(expected.code); }
            if (!rejected) throw new Failure("self_check_failed");
        }
    }
}
