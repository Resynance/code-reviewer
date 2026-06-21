import { useEffect, useState } from 'react'

export function useMediaQuery(query) {
  const getMatch = () => {
    if (typeof window === 'undefined' || !window.matchMedia) return false
    return window.matchMedia(query).matches
  }

  const [matches, setMatches] = useState(getMatch)

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const media = window.matchMedia(query)
    const onChange = () => setMatches(media.matches)
    onChange()
    if (media.addEventListener) media.addEventListener('change', onChange)
    else media.addListener(onChange)
    return () => {
      if (media.removeEventListener) media.removeEventListener('change', onChange)
      else media.removeListener(onChange)
    }
  }, [query])

  return matches
}
