// Author: JunyoungJung · 2026-10-29.
// Current-chat import only: no stored credential reads and no shell execution.
import childProcess from 'node:child_process';
import { isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT = fileURLToPath(new URL('./test_accounts.py', import.meta.url));
const OUTPUT_LIMIT = 64 * 1024;
const SLUG = /^[a-z0-9][a-z0-9-]{0,63}$/;
const OPTION_KEYS = new Set([
  'pythonExecutable', 'project', 'name', 'environment', 'role',
  'username', 'password', 'capabilities', 'relatedProjects', 'relatedEnvironments', 'timeoutMs',
]);
const STORAGE_CODES = new Set([
  'invalid_arguments', 'invalid_credentials', 'invalid_reference', 'foreign_reference',
  'storage_failed', 'storage_not_ready', 'migration_requires_approval', 'internal_error',
]);

function record(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    && Object.getPrototypeOf(value) === Object.prototype;
}

function text(value, limit) {
  return typeof value === 'string' && value.length > 0
    && Buffer.byteLength(value, 'utf8') <= limit
    && Buffer.from(value, 'utf8').toString('utf8') === value;
}

function absolutePath(value) {
  return text(value, 4096) && isAbsolute(value) && !value.includes('\0');
}

function list(value, check) {
  return Array.isArray(value) && value.length <= 64 && value.every(check);
}

function normalized(values) {
  return [...new Set(values)].sort();
}

function prepare(options) {
  if (!record(options) || Object.keys(options).some(key => !OPTION_KEYS.has(key))) return null;
  const { pythonExecutable, project, name, environment = 'default', role = 'tester', username, password } = options;
  const capabilities = options.capabilities ?? [];
  const relatedProjects = options.relatedProjects ?? [];
  const relatedEnvironments = options.relatedEnvironments ?? [];
  if (!absolutePath(pythonExecutable) || !absolutePath(project)
      || ![name, environment, role].every(value => typeof value === 'string' && SLUG.test(value))
      || !list(capabilities, value => typeof value === 'string' && SLUG.test(value))
      || !list(relatedProjects, absolutePath)
      || !list(relatedEnvironments, value => typeof value === 'string' && SLUG.test(value))
      || !text(username, 16384) || !text(password, 16384)) return null;
  const metadata = {
    pythonExecutable, name, environment, role,
    capabilities: normalized(capabilities), relatedProjects: normalized(relatedProjects),
    relatedEnvironments: normalized(relatedEnvironments.filter(value => value !== environment)),
  };
  const args = ['-E', '-s', '-B', SCRIPT, 'register', '--project', project, '--name', name,
    '--environment', environment, '--role', role, '--credentials-stdin'];
  for (const [flag, values] of [
    ['--capability', metadata.capabilities], ['--related-project', metadata.relatedProjects],
    ['--related-environment', metadata.relatedEnvironments],
  ]) for (const value of values) args.push(flag, value);
  return { metadata, args, payload: JSON.stringify({ username, password }) + '\n' };
}

function sameList(actual, expected) {
  return Array.isArray(actual) && JSON.stringify(actual) === JSON.stringify(expected);
}

function registeredAccount(account, metadata) {
  if (!record(account)) return false;
  const binding = Object.hasOwn(account, 'device_binding') ? 'device_binding' : 'download_device';
  const expected = ['id', 'project', 'environment', 'role', 'capabilities',
    'related_projects', 'related_environments', binding].sort();
  return sameList(Object.keys(account).sort(), expected)
    && account.id === metadata.name && account.environment === metadata.environment
    && account.role === metadata.role && text(account.project, 4096)
    && sameList(account.capabilities, metadata.capabilities)
    && sameList(account.related_environments, metadata.relatedEnvironments)
    && list(account.related_projects, value => text(value, 4096)) && account[binding] === null;
}

function safeResponse(chunks, code, signal, metadata) {
  const invalid = { status: 'uncertain', code: 'registration_response_invalid' };
  try {
    const response = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)));
    if (!record(response) || signal) return invalid;
    const keys = Object.keys(response).sort();
    if (code === 0 && response.status === 'registered' && sameList(keys, ['account', 'status'])
        && registeredAccount(response.account, metadata)) {
      return { status: 'registered', id: metadata.name, environment: metadata.environment, role: metadata.role };
    }
    if ([2, 130].includes(code) && response.status === 'cancelled' && sameList(keys, ['status'])) {
      return { status: 'cancelled', registrationUnconfirmed: true };
    }
    if (code === 1 && response.status === 'error'
        && (sameList(keys, ['message', 'status']) || sameList(keys, ['code', 'message', 'status']))
        && text(response.message, 4096)) {
      return { status: 'error', code: STORAGE_CODES.has(response.code) ? response.code : 'registration_failed',
        registrationUnconfirmed: true };
    }
  } catch { /* Raw child output and exception messages never leave this function. */ }
  return invalid;
}

function run(prepared) {
  const { metadata, args, payload } = prepared;
  return new Promise(resolve => {
    let child;
    try {
      child = childProcess.spawn(metadata.pythonExecutable, args, {
        shell: false, stdio: ['pipe', 'pipe', 'ignore'],
      });
    } catch {
      resolve({ status: 'error', code: 'registration_unavailable' });
      return;
    }
    let settled = false;
    let bytes = 0;
    const chunks = [];
    let failure;
    function finish(result) {
      if (settled) return;
      settled = true;
      chunks.length = 0;
      resolve(result);
    }
    child.on('error', () => finish({ status: 'error', code: 'registration_unavailable' }));
    child.stdin.on('error', () => { failure = { status: 'uncertain', code: 'registration_input_failed' }; });
    child.stdout.on('error', () => { failure = { status: 'uncertain', code: 'registration_response_invalid' }; });
    child.stdout.on('data', chunk => {
      if (settled) return;
      bytes += chunk.length;
      if (bytes > OUTPUT_LIMIT) {
        failure = { status: 'uncertain', code: 'registration_response_invalid' };
        chunks.length = 0;
      } else chunks.push(chunk);
    });
    child.on('close', (code, signal) => {
      if (!settled) finish(failure ?? safeResponse(chunks, code, signal, metadata));
    });
    try {
      child.stdin.end(payload, 'utf8');
    } catch {
      failure = { status: 'uncertain', code: 'registration_input_failed' };
    }
  });
}

/** Import values explicitly supplied in the current chat; return safe metadata only.
 * pythonExecutable must be an already verified absolute Python 3.10+ executable.
 * Chat/tool histories and JavaScript memory are outside the local store's guarantees.
 */
export async function registerChatAccount(options) {
  let prepared;
  try { prepared = prepare(options); } catch { return { status: 'error', code: 'invalid_arguments' }; }
  if (!prepared) return { status: 'error', code: 'invalid_arguments' };
  return run(prepared);
}
