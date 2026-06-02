import { NavLink } from "react-router";
import {
  LayoutDashboard,
  Upload,
  Database,
  MessageSquare,
  Activity,
  ClipboardCheck,
  Settings,
} from "lucide-react";

const navItems = [
  { path: "/", label: "文件统计", icon: LayoutDashboard, exact: true },
  { path: "/ingestion", label: "文档入库", icon: Upload },
  { path: "/index", label: "索引管理", icon: Database },
  { path: "/query", label: "问答工作台", icon: MessageSquare },
  { path: "/retrieval", label: "检索诊断", icon: Activity },
  { path: "/evaluation", label: "评测", icon: ClipboardCheck },
  { path: "/settings", label: "设置", icon: Settings },
];

export function Sidebar() {
  return (
    <aside className="w-56 border-r bg-card flex flex-col">
      <div className="p-4 border-b">
        <h2 className="font-semibold text-sm">LangGraph RAG</h2>
        <p className="text-xs text-muted-foreground mt-0.5">前端工作台</p>
      </div>

      <nav className="flex-1 p-2 space-y-0.5">
        {navItems.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.exact}
              className={({ isActive }) =>
                `flex items-center gap-2.5 px-3 py-2 rounded text-sm transition-colors ${
                  isActive
                    ? "bg-primary/10 text-primary font-medium"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                }`
              }
            >
              <Icon className="h-4 w-4" />
              {item.label}
            </NavLink>
          );
        })}
      </nav>
    </aside>
  );
}
