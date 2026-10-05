import { ArrowRight } from 'lucide-react'
import { useId, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { z } from 'zod'
import { Button } from '@/components/ui/button'
import { RecordSelect } from '@/components/record-select'
import { Label } from '@/components/ui/label'

export function OpenRunForm() {
  const id = useId()
  const navigate = useNavigate()
  const [value, setValue] = useState('')
  const [error, setError] = useState(false)
  return (
    <form
      aria-label="Open run"
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault()
        const parsed = z.uuid().safeParse(value.trim())
        if (!parsed.success) {
          setError(true)
          return
        }
        setError(false)
        navigate(`/runs/${parsed.data}`)
      }}
    >
      <Label htmlFor={id} className="text-sm font-medium">
        Saved run
      </Label>
      <RecordSelect
        kind="runs"
        placeholder="Choose a saved run"
        id={id}
        required
        value={value}
        onChange={(event) => setValue(event.target.value)}
        aria-invalid={error}
      />
      {error && (
        <p role="alert" className="text-sm text-amber-700">
          Choose an available run.
        </p>
      )}
      <Button type="submit" variant="outline" size="sm">
        <ArrowRight />
        Open run
      </Button>
    </form>
  )
}
