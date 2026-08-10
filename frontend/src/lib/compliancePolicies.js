export const DEFAULT_COMPLIANCE_POLICIES = { default: {}, repos: {} }

export function formatCompliancePolicies(policies) {
  return JSON.stringify(policies || DEFAULT_COMPLIANCE_POLICIES, null, 2)
}

export function parseCompliancePolicies(input) {
  const parsed = JSON.parse(input || '{}')
  return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
    ? parsed
    : DEFAULT_COMPLIANCE_POLICIES
}

export function setRepoComplianceEnabledInPolicy(policies, repo, enabled) {
  const current = policies && typeof policies === 'object' && !Array.isArray(policies)
    ? policies
    : DEFAULT_COMPLIANCE_POLICIES
  const repos = current.repos && typeof current.repos === 'object' && !Array.isArray(current.repos)
    ? current.repos
    : {}
  const repoPolicy = repos[repo] && typeof repos[repo] === 'object' && !Array.isArray(repos[repo])
    ? repos[repo]
    : {}

  return {
    ...current,
    repos: {
      ...repos,
      [repo]: {
        ...repoPolicy,
        enabled,
      },
    },
  }
}
