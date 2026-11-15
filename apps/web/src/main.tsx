import React from "react";
import { createRoot } from "react-dom/client";
import "katex/dist/katex.min.css";
import "./styles.css";
import App from "./App";

/** 错误摘要最长保留多少字符：整段堆栈留给控制台，界面只给一句能看懂的原因 */
const MAX_SUMMARY_CHARS = 240;

type BoundaryState = { error: Error | null };

/**
 * 渲染期兜底：真实模型返回脏数据时，任何组件抛错都由这里接住，
 * 显示「界面出错了」+ 错误摘要 + 重新加载按钮，而不是整页白屏。
 */
class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  BoundaryState
> {
  state: BoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // 完整堆栈留在控制台，方便复现时定位组件
    console.error("界面渲染出错：", error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const summary = (error.message || String(error)).slice(0, MAX_SUMMARY_CHARS);
    return (
      <div className="fatal" role="alert">
        <h2>界面出错了</h2>
        <p className="fatal-summary">{summary || "未知错误"}</p>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => window.location.reload()}
        >
          重新加载
        </button>
        <p className="fatal-hint">
          若刷新后仍出现，请查看后端日志；已有数据不会因刷新丢失。
        </p>
      </div>
    );
  }
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);
