import { Component, type ErrorInfo, type ReactNode } from "react";

type State = { error: Error | null };

/** A page that throws while rendering shows this instead of a blank window. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("畫面發生錯誤", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="app-shell">
        <main className="page">
          <h1 className="h1">畫面發生錯誤</h1>
          <p role="alert">{error.message || String(error)}</p>
          <div className="actions">
            <button className="btn" type="button" onClick={() => window.location.reload()}>重新載入</button>
            <a className="btn alt" href="/">回首頁</a>
          </div>
        </main>
      </div>
    );
  }
}
