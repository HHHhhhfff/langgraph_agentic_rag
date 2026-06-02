import { Moon, Sun } from "lucide-react";
import { Button } from "../ui/button";
import { useEffect, useState } from "react";

export function TopBar() {
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", isDark);
  }, [isDark]);

  return (
    <header className="h-14 border-b bg-card px-4 flex items-center justify-between">
      <h1 className="font-semibold text-sm">LangGraph Agentic RAG 工作台</h1>

      <Button
        variant="ghost"
        size="icon"
        className="h-8 w-8"
        onClick={() => setIsDark(!isDark)}
        aria-label="切换主题"
      >
        {isDark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
      </Button>
    </header>
  );
}
