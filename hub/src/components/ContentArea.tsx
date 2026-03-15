import { useEffect, useRef, useState } from 'react'
import { FileX } from 'lucide-react'

interface ContentAreaProps {
  currentPage: string | null
  theme: 'light' | 'dark'
}

export default function ContentArea({ currentPage, theme }: ContentAreaProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [loadError, setLoadError] = useState(false)

  // Reset error state when page changes
  useEffect(() => {
    setLoadError(false)
  }, [currentPage])

  // Send theme changes to iframe via postMessage
  useEffect(() => {
    const iframe = iframeRef.current
    if (iframe?.contentWindow) {
      iframe.contentWindow.postMessage({ type: 'soy-theme', theme }, '*')
    }
  }, [theme])

  if (!currentPage) {
    return null
  }

  if (loadError) {
    const label = currentPage.replace('.html', '').replace(/-/g, ' ')
    return (
      <main className="flex-1 min-h-screen flex items-center justify-center">
        <div className="text-center max-w-sm">
          <FileX className="w-10 h-10 text-zinc-400 dark:text-zinc-500 mx-auto mb-3" />
          <h2 className="text-lg font-semibold text-zinc-900 dark:text-zinc-100 capitalize">{label}</h2>
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-1">
            This view hasn't been generated yet. Ask me to create it.
          </p>
        </div>
      </main>
    )
  }

  const src = `/pages/${currentPage}?raw=1&theme=${theme}`

  return (
    <main className="flex-1 min-h-screen">
      <iframe
        ref={iframeRef}
        key={currentPage}
        src={src}
        className="w-full h-screen border-0"
        title={currentPage}
        onLoad={() => {
          const iframe = iframeRef.current
          if (iframe?.contentWindow) {
            // Check if the iframe loaded a 404 error page
            try {
              const doc = iframe.contentWindow.document
              const body = doc?.body?.textContent || ''
              if (body.includes('"error"') && body.includes('Not found')) {
                setLoadError(true)
                return
              }
            } catch {
              // Cross-origin — page loaded fine
            }
            iframe.contentWindow.postMessage({ type: 'soy-theme', theme }, '*')
          }
        }}
      />
    </main>
  )
}
