// Author: JunyoungJung · 2026-10-27. Synthetic credentials only.
import assert from 'node:assert/strict';
import childProcess from 'node:child_process';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { registerChatAccount } from '../scripts/chat_registration.mjs';

const SYNTHETIC_PASSWORD = ' synthetic-only \n한글\\$()`value`\t ';
const credentialFixture = {
  username: '  synthetic 사용자\n"quote"  ',
  password: SYNTHETIC_PASSWORD,
};
const options = {
  pythonExecutable: '/synthetic/python3', project: '/synthetic/project',
  name: 'sample-staging-tester', environment: 'staging', role: 'tester',
  capabilities: ['smoke', 'ios', 'smoke'], ...credentialFixture,
};

function response(changes = {}) {
  return {
    status: 'registered',
    account: {
      id: options.name, project: 'example.invalid/synthetic/project',
      environment: options.environment, role: options.role,
      capabilities: ['ios', 'smoke'], related_projects: [], related_environments: [],
      device_binding: null,
    }, ...changes,
  };
}

function mockChild(t, { output = response(), code = 0, idle = false, spawnError = false } = {}) {
  const child = new EventEmitter();
  child.stdin = new PassThrough();
  child.stdout = new PassThrough();
  const input = [];
  const signals = [];
  child.stdin.on('data', chunk => input.push(chunk));
  child.kill = signal => {
    signals.push(signal);
    queueMicrotask(() => child.emit('close', null, signal));
    return true;
  };
  const spawn = t.mock.method(childProcess, 'spawn', () => {
    queueMicrotask(() => {
      if (spawnError) child.emit('error', new Error(credentialFixture.password));
      else if (!idle) {
        child.stdout.end(typeof output === 'string' ? output : JSON.stringify(output) + '\n');
        child.emit('close', code, null);
      }
    });
    return child;
  });
  return { child, input, signals, spawn };
}

test('preserves exact chat values in one JSON stdin line with metadata-only argv', async t => {
  const { input, spawn } = mockChild(t);
  assert.deepEqual(await registerChatAccount(options), {
    status: 'registered', id: options.name, environment: 'staging', role: 'tester',
  });
  const [executable, args, spawnOptions] = spawn.mock.calls[0].arguments;
  assert.equal(executable, options.pythonExecutable);
  assert.deepEqual(args.slice(0, 3), ['-E', '-s', '-B']);
  assert.ok(args[3].endsWith('/scripts/test_accounts.py'));
  assert.deepEqual(args.slice(4), [
    'register', '--project', options.project, '--name', options.name,
    '--environment', 'staging', '--role', 'tester', '--credentials-stdin',
    '--capability', 'ios', '--capability', 'smoke',
  ]);
  assert.equal(spawnOptions.shell, false);
  assert.deepEqual(spawnOptions.stdio, ['pipe', 'pipe', 'ignore']);
  assert.equal('env' in spawnOptions, false);
  for (const value of Object.values(credentialFixture)) assert.equal(JSON.stringify(args).includes(value), false);
  const payload = Buffer.concat(input).toString('utf8');
  assert.equal(payload.split('\n').length, 2);
  assert.deepEqual(JSON.parse(payload), credentialFixture);
});

test('supports related metadata without exposing its response beyond the alias', async t => {
  const { spawn } = mockChild(t, { output: response({ account: {
    ...response().account,
    related_projects: ['example.invalid/synthetic/other'], related_environments: ['development'],
  } }) });
  const result = await registerChatAccount({ ...options,
    relatedProjects: ['/synthetic/other'], relatedEnvironments: ['development'],
  });
  assert.equal(result.status, 'registered');
  assert.deepEqual(spawn.mock.calls[0].arguments[1].slice(-4), [
    '--related-project', '/synthetic/other', '--related-environment', 'development',
  ]);
  assert.equal('project' in result, false);
});

test('rejects invalid metadata, malformed options and credentials before spawning', async t => {
  const { spawn } = mockChild(t);
  const invalid = [
    null, {}, { ...options, pythonExecutable: 'python3' }, { ...options, project: 'relative' },
    { ...options, name: '-bad' }, { ...options, environment: '' }, { ...options, role: 'Admin' },
    { ...options, capabilities: ['valid', 'BAD'] }, { ...options, relatedProjects: ['relative'] },
    { ...options, relatedEnvironments: ['BAD'] }, { ...options, username: '' },
    { ...options, password: 123 }, { ...options, password: '\ud800' },
    { ...options, password: '한'.repeat(6000) }, { ...options, timeoutMs: 0 },
    { ...options, unexpected: credentialFixture.password },
  ];
  for (const value of invalid) assert.deepEqual(await registerChatAccount(value), {
    status: 'error', code: 'invalid_arguments',
  });
  assert.equal(spawn.mock.callCount(), 0);
});

test('returns safe cancellation and known storage codes only', async t => {
  const cases = [
    { output: { status: 'cancelled' }, code: 2, expected: { status: 'cancelled', registrationUnconfirmed: true } },
    { output: { status: 'error', code: 'invalid_credentials', message: credentialFixture.password }, code: 1,
      expected: { status: 'error', code: 'invalid_credentials', registrationUnconfirmed: true } },
    { output: { status: 'error', code: credentialFixture.password, message: credentialFixture.password }, code: 1,
      expected: { status: 'error', code: 'registration_failed', registrationUnconfirmed: true } },
  ];
  for (const entry of cases) {
    const mocked = mockChild(t, entry);
    assert.deepEqual(await registerChatAccount(options), entry.expected);
    mocked.spawn.mock.restore();
  }
});

test('rejects malformed, excessive, unexpected and mismatched responses safely', async t => {
  const cases = [
    credentialFixture.password, '{}\n{}', 'x'.repeat(65537),
    response({ account: { ...response().account, id: 'other-account' } }),
    response({ account: { ...response().account, keychain_ref: credentialFixture.password } }),
    response({ account: { ...response().account, capabilities: ['admin'] } }),
    response({ account: { ...response().account, device_binding: { identifier: credentialFixture.password } } }),
    response({ username: credentialFixture.username }),
  ];
  for (const output of cases) {
    const mocked = mockChild(t, { output });
    const result = await registerChatAccount(options);
    assert.equal(result.status, 'uncertain');
    assert.equal(JSON.stringify(result).includes(credentialFixture.password), false);
    mocked.spawn.mock.restore();
  }
});

test('does not promote success-shaped output with nonzero exit to registration success', async t => {
  mockChild(t, { code: 1 });
  assert.deepEqual(await registerChatAccount(options), { status: 'uncertain', code: 'registration_response_invalid' });
});

test('maps spawn failures to safe errors without printing exception text', async t => {
  mockChild(t, { spawnError: true });
  assert.deepEqual(await registerChatAccount(options), { status: 'error', code: 'registration_unavailable' });
});

test('maps synchronous spawn failures to safe errors', async t => {
  t.mock.method(childProcess, 'spawn', () => { throw new Error(credentialFixture.password); });
  assert.deepEqual(await registerChatAccount(options), { status: 'error', code: 'registration_unavailable' });
});

test('handles stdin failures without exposing the payload or retrying', async t => {
  const { child, signals, spawn } = mockChild(t, { idle: true });
  const pending = registerChatAccount(options);
  child.stdin.emit('error', new Error(credentialFixture.password));
  assert.deepEqual(await pending, { status: 'uncertain', code: 'registration_input_failed' });
  assert.deepEqual(signals, ['SIGTERM']);
  assert.equal(spawn.mock.callCount(), 1);
});

test('bounds its wait, stops its own child and reports an uncertain save outcome', async t => {
  const { signals } = mockChild(t, { idle: true });
  assert.deepEqual(await registerChatAccount({ ...options, timeoutMs: 5 }), {
    status: 'uncertain', code: 'registration_timed_out',
  });
  assert.deepEqual(signals, ['SIGTERM']);
});
