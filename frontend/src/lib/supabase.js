import { createClient } from '@supabase/supabase-js'

// Auth is enabled only when the Supabase env vars are present at build time.
// Locally (no VITE_SUPABASE_URL) the app runs without auth, exactly as before.
const url = import.meta.env.VITE_SUPABASE_URL
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

export const authEnabled = Boolean(url && anonKey)
export const supabase = authEnabled ? createClient(url, anonKey) : null
