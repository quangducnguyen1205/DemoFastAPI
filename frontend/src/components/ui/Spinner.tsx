type SpinnerSize = 'sm' | 'md' | 'lg'

interface SpinnerProps {
  size?: SpinnerSize
  className?: string
  color?: 'brand' | 'white' | 'gray'
}

const sizeClasses: Record<SpinnerSize, string> = {
  sm: 'h-4 w-4',
  md: 'h-6 w-6',
  lg: 'h-8 w-8',
}

const colorClasses: Record<string, { track: string; spinner: string }> = {
  brand: { track: 'text-brand-200', spinner: 'text-brand-600' },
  white: { track: 'text-white/30', spinner: 'text-white' },
  gray: { track: 'text-gray-200', spinner: 'text-gray-500' },
}

export function Spinner({ size = 'md', className = '', color = 'brand' }: SpinnerProps) {
  const colors = colorClasses[color]

  return (
    <div className={`relative ${sizeClasses[size]} ${className}`}>
      {/* Track circle */}
      <svg
        className={`absolute inset-0 ${colors.track}`}
        viewBox="0 0 24 24"
        fill="none"
      >
        <circle
          cx="12"
          cy="12"
          r="10"
          stroke="currentColor"
          strokeWidth="3"
        />
      </svg>
      {/* Spinning arc */}
      <svg
        className={`animate-spin ${colors.spinner}`}
        viewBox="0 0 24 24"
        fill="none"
      >
        <circle
          cx="12"
          cy="12"
          r="10"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
          strokeDasharray="32"
          strokeDashoffset="8"
        />
      </svg>
    </div>
  )
}

export default Spinner
