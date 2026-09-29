import { useQuery } from '@tanstack/react-query'

import { getHealth } from '@/services/health'

function App() {
  const { data, isError } = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
  })

  const connectionStatus = data
    ? 'API connected'
    : isError
      ? 'Start the API on port 8000'
      : 'Connecting to API'

  return (
    <main className="flex min-h-svh items-center justify-center bg-background px-6 text-foreground">
      <div className="text-center">
        <h1 className="text-4xl font-semibold tracking-normal sm:text-5xl">
          CarbonMesh
        </h1>
        <p className="mt-3 text-sm text-muted-foreground" aria-live="polite">
          {connectionStatus}
        </p>
      </div>
    </main>
  )
}

export default App
