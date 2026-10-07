"use client";

import * as React from "react";

interface Props {
  /** What to show when a child throws. Receives the error. */
  fallback: (error: Error) => React.ReactNode;
  /** Changing this value clears a caught error, so a new response gets a fresh try. */
  resetKey?: unknown;
  children: React.ReactNode;
}

/** Catches a renderer that throws on an unexpected specification, so the page never goes blank. */
export class ErrorBoundary extends React.Component<Props, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidUpdate(previous: Props) {
    if (this.state.error && previous.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render() {
    return this.state.error ? this.props.fallback(this.state.error) : this.props.children;
  }
}
