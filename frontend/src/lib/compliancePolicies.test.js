import assert from 'node:assert/strict'
import test from 'node:test'

import {
  formatCompliancePolicies,
  parseCompliancePolicies,
  setRepoComplianceEnabledInPolicy,
} from './compliancePolicies.js'

test('repo compliance toggle preserves existing policy fields', () => {
  const current = {
    default: { enabled: false, phi_field_patterns: ['patient_id'] },
    repos: {
      'org/app': { enabled: false, approved_vendors: ['aws'] },
    },
  }

  const next = setRepoComplianceEnabledInPolicy(current, 'org/app', true)

  assert.deepEqual(next.default, current.default)
  assert.deepEqual(next.repos['org/app'], {
    enabled: true,
    approved_vendors: ['aws'],
  })
})

test('repo compliance toggle keeps unrelated unsaved repo policies', () => {
  const current = parseCompliancePolicies(formatCompliancePolicies({
    default: {},
    repos: {
      'org/other': { enabled: true, required_auth_signals: ['require_user'] },
    },
  }))

  const next = setRepoComplianceEnabledInPolicy(current, 'org/app', true)

  assert.deepEqual(next.repos['org/other'], { enabled: true, required_auth_signals: ['require_user'] })
  assert.deepEqual(next.repos['org/app'], { enabled: true })
})

test('policy parser rejects invalid JSON', () => {
  assert.throws(() => parseCompliancePolicies('{not json}'), SyntaxError)
})
