import { Badge } from "../ui/badge";
import { Circle, CheckCircle2, XCircle, AlertCircle, Clock, Minus } from "lucide-react";

interface StatusBadgeProps {
  status: "success" | "error" | "warning" | "running" | "pending" | "skipped";
  label?: string;
  className?: string;
}

const statusConfig = {
  success: {
    icon: CheckCircle2,
    className: "bg-green-100 text-green-700 border-green-200",
    label: "成功",
  },
  error: {
    icon: XCircle,
    className: "bg-red-100 text-red-700 border-red-200",
    label: "错误",
  },
  warning: {
    icon: AlertCircle,
    className: "bg-yellow-100 text-yellow-700 border-yellow-200",
    label: "警告",
  },
  running: {
    icon: Circle,
    className: "bg-blue-100 text-blue-700 border-blue-200",
    label: "运行中",
  },
  pending: {
    icon: Clock,
    className: "bg-gray-100 text-gray-700 border-gray-200",
    label: "等待中",
  },
  skipped: {
    icon: Minus,
    className: "bg-gray-100 text-gray-500 border-gray-200",
    label: "已跳过",
  },
};

export function StatusBadge({ status, label, className }: StatusBadgeProps) {
  const config = statusConfig[status];
  const Icon = config.icon;

  return (
    <Badge variant="outline" className={`${config.className} ${className || ""}`}>
      <Icon className="h-3 w-3 mr-1" />
      {label || config.label}
    </Badge>
  );
}
