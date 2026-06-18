import { supabase, authEnabled } from './supabase.js'

// Keep the current access token in memory so api.js can attach it synchronously.
let _token = null

if (authEnabled) {
  supabase.auth.getSession().then(({ data }) => {
    _token = data.session?.access_token || null
  })
  supabase.auth.onAuthStateChange((_event, session) => {
    _token = session?.access_token || null
  })
}

export { authEnabled, supabase }

export function accessToken() {
  return _token
}

export function signIn(email, password) {
  return supabase.auth.signInWithPassword({ email, password })
}

export function signOut() {
  return supabase.auth.signOut()
}
