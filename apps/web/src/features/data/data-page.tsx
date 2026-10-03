import { Database, FileText, Upload } from 'lucide-react'
import { useOutletContext, useSearchParams } from 'react-router-dom'

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { WorkspaceScope } from '@/lib/workspace'
import { FactorPanel } from './factor-panel'
import { ImportPanel } from './import-panel'
import { WorkspaceProvenance } from './intake-ui'
import { SourcePanel } from './source-panel'

function DataIntake() {
  const [params, setParams] = useSearchParams()
  const requested = params.get('view')
  const view =
    requested && ['imports', 'documents', 'factors'].includes(requested)
      ? requested
      : params.has('document') || params.has('document_id')
        ? 'documents'
        : 'imports'
  return (
    <div className="min-w-0 space-y-5 px-5 py-6 sm:px-8">
      <header>
        <h1 className="text-2xl font-semibold">Data intake</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Activity records, source evidence and emission factors
        </p>
      </header>
      <WorkspaceProvenance />
      <Tabs
        value={view}
        onValueChange={(value) => {
          const next = new URLSearchParams(params)
          next.set('view', value)
          setParams(next)
        }}
        className="min-w-0 gap-0"
      >
        <div className="overflow-x-auto pb-3">
          <TabsList aria-label="Data intake">
            <TabsTrigger value="imports">
              <Upload className="size-4" />
              Imports
            </TabsTrigger>
            <TabsTrigger value="documents">
              <FileText className="size-4" />
              Documents
            </TabsTrigger>
            <TabsTrigger value="factors">
              <Database className="size-4" />
              Factors
            </TabsTrigger>
          </TabsList>
        </div>
        <TabsContent
          value="imports"
          forceMount
          className="min-w-0 data-[state=inactive]:hidden"
        >
          <ImportPanel />
        </TabsContent>
        <TabsContent value="documents" className="min-w-0">
          <SourcePanel />
        </TabsContent>
        <TabsContent value="factors" className="min-w-0">
          <FactorPanel />
        </TabsContent>
      </Tabs>
    </div>
  )
}

export default function DataPage() {
  const scope = useOutletContext<WorkspaceScope>()
  return (
    <DataIntake
      key={`${scope.company_id}:${scope.site_id}:${scope.reporting_period_id}`}
    />
  )
}
