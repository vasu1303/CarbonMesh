import { ArrowRight, Building2, MapPin } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { formatDate, humanize } from '@/lib/format'
import type { Supplier } from '../schemas'

export function SupplierList({
  items,
  onProducts,
}: {
  items: Supplier[]
  onProducts: (id: string) => void
}) {
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {items.map((supplier) => (
        <Card
          key={supplier.id}
          className="min-w-0 rounded-lg shadow-none transition-colors hover:border-foreground/25"
        >
            <CardHeader className="min-w-0 gap-3 wrap-anywhere">
            <div className="flex items-center justify-between gap-3">
              <Building2 className="size-5 text-muted-foreground" />
              <Badge variant="outline" className="capitalize">
                {humanize(supplier.status)}
              </Badge>
            </div>
              <CardTitle className="min-w-0 text-base leading-6">
              {supplier.name}
            </CardTitle>
            <p className="break-all font-mono text-xs text-muted-foreground">
              {supplier.supplier_code}
            </p>
          </CardHeader>
          <CardContent className="flex flex-1 flex-col gap-4">
            <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1">
                <MapPin className="size-3.5" />
                {supplier.country_code}
              </span>
              <span className="capitalize">
                Risk: {humanize(supplier.risk)}
              </span>
            </div>
            <dl className="grid grid-cols-2 gap-3 border-y py-4">
              <div>
                <dt className="text-xs text-muted-foreground">Products</dt>
                <dd className="mt-1 font-mono text-xl tabular-nums">
                  {supplier.product_count}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">
                  Active products
                </dt>
                <dd className="mt-1 font-mono text-xl tabular-nums">
                  {supplier.active_product_count}
                </dd>
              </div>
            </dl>
            <p className="text-xs text-muted-foreground">
              Updated {formatDate(supplier.updated_at)}
            </p>
            <Button
              variant="ghost"
              size="sm"
              className="mt-auto w-full justify-between"
              onClick={() => onProducts(supplier.id)}
              aria-label={`Browse products from ${supplier.name}`}
            >
              Browse products
              <ArrowRight />
            </Button>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
