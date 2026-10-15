import assert from 'node:assert/strict'
import test from 'node:test'

import { severityColor, severityKey, severityLabel } from './severity.js'

test('severity helpers tolerate missing severity values', () => {
  assert.equal(severityKey(undefined), 'unknown')
  assert.equal(severityLabel(undefined), 'UNKNOWN')
  assert.equal(severityColor(undefined), 'var(--text-2)')
})

test('severity helpers normalize known severities', () => {
  assert.equal(severityKey(' High '), 'high')
  assert.equal(severityLabel('critical'), 'CRITICAL')
  assert.equal(severityColor('low'), '#6366F1')
})
