import { supabase, authEnabled } from './supabase.js'

// Keep the current access token in memory so api.js can attach it synchronously.
let _token = null

export function setAccessToken(token) {
  _token = token || null
}

if (authEnabled) {
  supabase.auth.getSession().then(({ data }) => {
    setAccessToken(data.session?.access_token)
  })
  supabase.auth.onAuthStateChange((_event, session) => {
    setAccessToken(session?.access_token)
  })
}

export { authEnabled, supabase }

export function accessToken() {
  return _token
}

export function signIn(email, password) {
  return supabase.auth.signInWithPassword({ email, password })
}

export function signInWithGitHub() {
  return supabase.auth.signInWithOAuth({
    provider: 'github',
    options: { redirectTo: window.location.origin },
  })
}

export function signOut() {
  return supabase.auth.signOut()
}
