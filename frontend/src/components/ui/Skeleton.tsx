interface SkeletonProps {
  className?: string
  width?: string | number
  height?: string | number
  rounded?: 'sm' | 'md' | 'lg' | 'xl' | '2xl' | 'full'
}

const roundedClasses = {
  sm: 'rounded-sm',
  md: 'rounded-md',
  lg: 'rounded-lg',
  xl: 'rounded-xl',
  '2xl': 'rounded-2xl',
  full: 'rounded-full',
}

export function Skeleton({
  className = '',
  width,
  height,
  rounded = 'lg',
}: SkeletonProps) {
  const style: React.CSSProperties = {}
  if (width) style.width = typeof width === 'number' ? `${width}px` : width
  if (height) style.height = typeof height === 'number' ? `${height}px` : height

  return (
    <div
      className={`
        animate-pulse bg-gray-200
        ${roundedClasses[rounded]}
        ${className}
      `.trim()}
      style={style}
    />
  )
}

interface SkeletonTextProps {
  lines?: number
  className?: string
}

export function SkeletonText({ lines = 3, className = '' }: SkeletonTextProps) {
  return (
    <div className={`space-y-2 ${className}`}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          height={16}
          width={i === lines - 1 ? '75%' : '100%'}
          rounded="md"
        />
      ))}
    </div>
  )
}

interface SkeletonCardProps {
  className?: string
}

export function SkeletonCard({ className = '' }: SkeletonCardProps) {
  return (
    <div
      className={`
        bg-app-surface rounded-2xl border border-app-border p-6
        ${className}
      `.trim()}
    >
      <div className="flex items-center mb-4">
        <Skeleton width={48} height={48} rounded="lg" className="mr-4" />
        <div className="flex-1">
          <Skeleton height={20} width="60%" rounded="md" className="mb-2" />
          <Skeleton height={16} width="40%" rounded="md" />
        </div>
      </div>
      <SkeletonText lines={2} />
    </div>
  )
}

export default Skeleton
