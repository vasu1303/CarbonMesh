import { FileCheck2, FileQuestion } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatDecimal, humanize } from '@/lib/format'
import type { Product } from '../schemas'

function ProductDetails({ product }: { product: Product }) {
  const facts = [
    [
      'Product carbon footprint',
      `${formatDecimal(product.pcf_kgco2e_per_unit)} ${product.pcf_unit}`,
    ],
    ['Unit cost', `${product.currency} ${formatDecimal(product.unit_cost)}`],
    ['Lead time', `${product.lead_time_days} days`],
    ['Circularity score', `${formatDecimal(product.circularity_score)} / 100`],
    ['Recycled content', `${formatDecimal(product.recycled_content_pct)}%`],
    ['Recyclable', `${formatDecimal(product.recyclable_pct)}%`],
    [
      'Evidence quality score',
      `${formatDecimal(product.evidence_quality_score)} / 100`,
    ],
    ['Material code', product.material_code],
    ['Category', product.category],
    ['Effective from', product.effective_from],
    ['Effective to', product.effective_to ?? 'No end date recorded'],
    ['Supplier status', humanize(product.supplier_status)],
    ['Supplier risk', humanize(product.risk)],
    ['Country', product.supplier_country_code],
  ]
  return (
    <DialogContent className="max-h-[85svh] overflow-y-auto rounded-lg wrap-anywhere sm:max-w-2xl">
      <DialogHeader className="min-w-0 pr-8">
        <DialogTitle className="min-w-0 text-lg leading-6">
          {product.name}
        </DialogTitle>
        <DialogDescription className="min-w-0">
          {product.supplier_name} / {product.product_code}
        </DialogDescription>
      </DialogHeader>
      {product.description && (
        <p className="min-w-0 text-sm text-muted-foreground">
          {product.description}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Badge variant="outline">
          {product.is_active ? 'Active product' : 'Inactive product'}
        </Badge>
        <Badge variant="secondary">Catalog record</Badge>
      </div>
      <dl className="grid min-w-0 grid-cols-1 gap-x-6 sm:grid-cols-2">
        {facts.map(([label, value]) => (
          <div key={label} className="min-w-0 border-b py-3">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="mt-1 text-sm font-medium tabular-nums">
              {value}
            </dd>
          </div>
        ))}
      </dl>
      <section aria-label="Product evidence" className="space-y-2 py-2">
        <h3 className="text-sm font-medium">
          {product.evidence_available
            ? 'Evidence linked'
            : 'No evidence linked'}
        </h3>
        {product.evidence_item_id && (
          <p className="break-all font-mono text-xs">
            {product.evidence_item_id}
          </p>
        )}
        <p className="text-xs text-muted-foreground">
          Catalog evidence linkage is not a verification or procurement
          approval. Evidence content and lineage are not included in this
          catalog response.
        </p>
      </section>
      <dl className="space-y-2 border-t pt-3 text-xs">
        <div>
          <dt className="text-muted-foreground">Product ID</dt>
          <dd className="mt-1 break-all font-mono">{product.id}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Supplier ID</dt>
          <dd className="mt-1 break-all font-mono">{product.supplier_id}</dd>
        </div>
      </dl>
    </DialogContent>
  )
}

export function ProductTable({ items }: { items: Product[] }) {
  return (
    <Table>
      <TableCaption className="text-left">
        Catalog values, not scored recommendations. Carbon units and currencies
        are recorded per product.
      </TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>Product / supplier</TableHead>
          <TableHead>Carbon footprint</TableHead>
          <TableHead>Unit cost</TableHead>
          <TableHead>Lead time</TableHead>
          <TableHead>Recycled</TableHead>
          <TableHead>Evidence</TableHead>
          <TableHead>Status</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((product) => (
          <TableRow key={product.id}>
            <TableCell className="min-w-56 max-w-80 whitespace-normal py-4 wrap-anywhere">
              <Dialog>
                <DialogTrigger asChild>
                  <Button
                    variant="link"
                    className="h-auto max-w-full justify-start p-0 text-left whitespace-normal wrap-anywhere"
                    aria-label={`Inspect ${product.name}`}
                  >
                    {product.name}
                  </Button>
                </DialogTrigger>
                <ProductDetails product={product} />
              </Dialog>
              <p className="mt-1 text-xs text-muted-foreground">
                {product.supplier_name}
              </p>
              <p className="mt-1 break-all font-mono text-xs text-muted-foreground">
                {product.product_code}
              </p>
            </TableCell>
            <TableCell>
              <p className="font-mono tabular-nums">
                {formatDecimal(product.pcf_kgco2e_per_unit)}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {product.pcf_unit}
              </p>
            </TableCell>
            <TableCell className="font-mono tabular-nums">
              {product.currency} {formatDecimal(product.unit_cost)}
            </TableCell>
            <TableCell className="tabular-nums">
              {product.lead_time_days} days
            </TableCell>
            <TableCell className="tabular-nums">
              {formatDecimal(product.recycled_content_pct)}%
            </TableCell>
            <TableCell>
              <span className="flex items-center gap-1.5 text-xs">
                {product.evidence_available ? (
                  <FileCheck2 className="size-4 text-emerald-700 dark:text-emerald-400" />
                ) : (
                  <FileQuestion className="size-4 text-amber-700 dark:text-amber-400" />
                )}
                {product.evidence_available ? 'Linked' : 'Missing'}
              </span>
            </TableCell>
            <TableCell>
              <Badge variant="outline">
                {product.is_active ? 'Active' : 'Inactive'}
              </Badge>
              {product.supplier_status !== 'active' && (
                <p className="mt-1 text-xs text-muted-foreground">
                  Supplier: {humanize(product.supplier_status)}
                </p>
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}
