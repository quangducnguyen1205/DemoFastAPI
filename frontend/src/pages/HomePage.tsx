import { Link } from 'react-router-dom'
import { Card, Button, Badge } from '../components/ui'

function HomePage() {
  return (
    <div className="relative overflow-hidden">
      {/* Background gradient blobs */}
      <div className="absolute inset-0 -z-10 overflow-hidden">
        <div className="absolute -top-40 -right-40 w-80 h-80 bg-brand-400/20 rounded-full blur-3xl" />
        <div className="absolute top-60 -left-40 w-96 h-96 bg-brand-300/15 rounded-full blur-3xl" />
        <div className="absolute bottom-20 right-20 w-72 h-72 bg-indigo-200/20 rounded-full blur-3xl" />
        {/* Subtle grid pattern */}
        <div
          className="absolute inset-0 opacity-[0.015]"
          style={{
            backgroundImage: `url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%23000000' fill-opacity='1'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E")`,
          }}
        />
      </div>

      {/* Hero Section */}
      <section className="py-16 lg:py-24">
        <div className="grid lg:grid-cols-2 gap-12 lg:gap-16 items-center">
          {/* Left: Hero Content */}
          <div className="text-center lg:text-left">
            <Badge variant="info" size="md" className="mb-6">
              AI-Powered Video Search
            </Badge>
            <h1 className="text-4xl sm:text-5xl lg:text-6xl font-bold text-gray-900 leading-tight mb-6">
              Search inside your{' '}
              <span className="text-brand-600">videos</span> with AI
            </h1>
            <p className="text-lg text-app-muted mb-8 max-w-lg mx-auto lg:mx-0">
              Upload any video, get it automatically transcribed, and instantly find 
              the exact moments you're looking for using natural language search.
            </p>
            <div className="flex flex-col sm:flex-row gap-4 justify-center lg:justify-start">
              <Link to="/register">
                <Button size="lg" className="w-full sm:w-auto">
                  Get started free
                </Button>
              </Link>
              <Link to="/login">
                <Button variant="secondary" size="lg" className="w-full sm:w-auto">
                  Login
                </Button>
              </Link>
            </div>
          </div>

          {/* Right: Preview Mock */}
          <div className="relative">
            <Card className="shadow-elevated">
              <div className="space-y-4">
                {/* Mock Search Input */}
                <div className="flex items-center gap-3 p-3 bg-gray-50 rounded-xl border border-app-border">
                  <svg className="w-5 h-5 text-app-muted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                  </svg>
                  <span className="text-gray-900 font-medium">How to configure the database?</span>
                </div>

                {/* Mock Results */}
                <div className="space-y-3">
                  <div className="p-3 rounded-xl border border-app-border hover:border-brand-200 hover:bg-brand-50/30 transition-colors cursor-pointer">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <p className="font-medium text-gray-900 truncate">Backend Setup Tutorial</p>
                        <p className="text-sm text-app-muted mt-1 line-clamp-2">
                          "...and then you'll need to configure the database connection string in your environment..."
                        </p>
                      </div>
                      <Badge variant="neutral" size="sm">2:34</Badge>
                    </div>
                  </div>

                  <div className="p-3 rounded-xl border border-app-border hover:border-brand-200 hover:bg-brand-50/30 transition-colors cursor-pointer">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <p className="font-medium text-gray-900 truncate">DevOps Best Practices</p>
                        <p className="text-sm text-app-muted mt-1 line-clamp-2">
                          "...the database migration should run automatically when you deploy..."
                        </p>
                      </div>
                      <Badge variant="neutral" size="sm">5:12</Badge>
                    </div>
                  </div>

                  <div className="p-3 rounded-xl border border-app-border hover:border-brand-200 hover:bg-brand-50/30 transition-colors cursor-pointer">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <p className="font-medium text-gray-900 truncate">Full Stack Workshop</p>
                        <p className="text-sm text-app-muted mt-1 line-clamp-2">
                          "...let me show you how to set up PostgreSQL for production..."
                        </p>
                      </div>
                      <Badge variant="neutral" size="sm">12:08</Badge>
                    </div>
                  </div>
                </div>

                {/* Results count */}
                <p className="text-sm text-app-muted text-center pt-2">
                  Found 3 relevant moments across your videos
                </p>
              </div>
            </Card>
          </div>
        </div>
      </section>

      {/* How It Works Section */}
      <section className="py-16 border-t border-app-border">
        <div className="text-center mb-12">
          <h2 className="text-3xl font-bold text-gray-900 mb-4">How it works</h2>
          <p className="text-app-muted max-w-2xl mx-auto">
            Get from raw video to searchable content in three simple steps.
          </p>
        </div>

        <div className="grid md:grid-cols-3 gap-8">
          {/* Step 1 */}
          <Card className="text-center">
            <div className="w-14 h-14 bg-brand-100 rounded-2xl flex items-center justify-center mx-auto mb-4">
              <svg className="w-7 h-7 text-brand-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
              </svg>
            </div>
            <Badge variant="info" size="sm" className="mb-3">Step 1</Badge>
            <h3 className="text-xl font-semibold text-gray-900 mb-2">Upload</h3>
            <p className="text-app-muted">
              Drag and drop any video file. We support all major formats.
            </p>
          </Card>

          {/* Step 2 */}
          <Card className="text-center">
            <div className="w-14 h-14 bg-brand-100 rounded-2xl flex items-center justify-center mx-auto mb-4">
              <svg className="w-7 h-7 text-brand-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <Badge variant="info" size="sm" className="mb-3">Step 2</Badge>
            <h3 className="text-xl font-semibold text-gray-900 mb-2">Transcribe</h3>
            <p className="text-app-muted">
              AI automatically converts speech to searchable text segments.
            </p>
          </Card>

          {/* Step 3 */}
          <Card className="text-center">
            <div className="w-14 h-14 bg-brand-100 rounded-2xl flex items-center justify-center mx-auto mb-4">
              <svg className="w-7 h-7 text-brand-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
            <Badge variant="info" size="sm" className="mb-3">Step 3</Badge>
            <h3 className="text-xl font-semibold text-gray-900 mb-2">Search</h3>
            <p className="text-app-muted">
              Use natural language to find exactly what you're looking for.
            </p>
          </Card>
        </div>
      </section>

      {/* Features Section */}
      <section className="py-16 border-t border-app-border">
        <div className="text-center mb-12">
          <h2 className="text-3xl font-bold text-gray-900 mb-4">Powerful features</h2>
          <p className="text-app-muted max-w-2xl mx-auto">
            Everything you need to unlock the knowledge hidden in your videos.
          </p>
        </div>

        <div className="grid md:grid-cols-3 gap-8">
          {/* Feature 1 */}
          <Card>
            <div className="w-12 h-12 bg-green-100 rounded-xl flex items-center justify-center mb-4">
              <svg className="w-6 h-6 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
            </div>
            <h3 className="text-lg font-semibold text-gray-900 mb-2">Lightning Fast</h3>
            <p className="text-app-muted">
              Get search results in milliseconds with our optimized vector database.
            </p>
          </Card>

          {/* Feature 2 */}
          <Card>
            <div className="w-12 h-12 bg-purple-100 rounded-xl flex items-center justify-center mb-4">
              <svg className="w-6 h-6 text-purple-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
              </svg>
            </div>
            <h3 className="text-lg font-semibold text-gray-900 mb-2">Semantic Understanding</h3>
            <p className="text-app-muted">
              Find content by meaning, not just keywords. Ask questions naturally.
            </p>
          </Card>

          {/* Feature 3 */}
          <Card>
            <div className="w-12 h-12 bg-amber-100 rounded-xl flex items-center justify-center mb-4">
              <svg className="w-6 h-6 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
              </svg>
            </div>
            <h3 className="text-lg font-semibold text-gray-900 mb-2">Private & Secure</h3>
            <p className="text-app-muted">
              Your videos stay yours. All processing happens in your private workspace.
            </p>
          </Card>
        </div>
      </section>

      {/* Final CTA */}
      <section className="py-16 border-t border-app-border">
        <Card className="bg-gradient-to-br from-brand-600 to-brand-700 border-0 text-center">
          <h2 className="text-2xl sm:text-3xl font-bold text-white mb-4">
            Ready to search your videos?
          </h2>
          <p className="text-brand-100 mb-8 max-w-xl mx-auto">
            Join thousands of users who save hours every week finding exactly 
            what they need in their video libraries.
          </p>
          <Link to="/register">
            <Button
              variant="secondary"
              size="lg"
              className="bg-white text-brand-600 hover:bg-brand-50 border-0"
            >
              Start for free
            </Button>
          </Link>
        </Card>
      </section>
    </div>
  )
}

export default HomePage
