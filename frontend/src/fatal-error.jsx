import React from "react";

/** Render a consistent fatal application error without requiring theme state. */
function FatalErrorScreen({ message }) {
  return (
    <div
      role="alert"
      style={{
        width: "100%",
        height: "100%",
        colorScheme: "light dark",
        background: "Canvas",
        color: "CanvasText",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 32,
        fontFamily: "inherit",
        fontSize: 14,
        lineHeight: 1.5,
        textAlign: "center",
      }}
    >
      {message} Restart Fenestra. If the problem continues, reinstall the application.
    </div>
  );
}

/** Keep an unexpected React render failure from leaving a blank webview. */
class FenestraErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("[main] The Fenestra interface crashed:", error, info);
  }

  render() {
    if (this.state.error) {
      return <FatalErrorScreen message="Fenestra encountered an interface error." />;
    }
    return this.props.children;
  }
}

export { FatalErrorScreen, FenestraErrorBoundary };
