import { Badge } from "../ui/badge";
import { FileText, Table, Calculator, Image } from "lucide-react";

interface ModalityBadgeProps {
  modality: "text" | "table" | "formula" | "image";
  className?: string;
}

const modalityConfig = {
  text: {
    icon: FileText,
    label: "文本",
    className: "bg-blue-100 text-blue-700 border-blue-200",
  },
  table: {
    icon: Table,
    label: "表格",
    className: "bg-green-100 text-green-700 border-green-200",
  },
  formula: {
    icon: Calculator,
    label: "公式",
    className: "bg-purple-100 text-purple-700 border-purple-200",
  },
  image: {
    icon: Image,
    label: "图片",
    className: "bg-orange-100 text-orange-700 border-orange-200",
  },
};

export function ModalityBadge({ modality, className }: ModalityBadgeProps) {
  const config = modalityConfig[modality];
  const Icon = config.icon;

  return (
    <Badge variant="outline" className={`${config.className} ${className || ""}`}>
      <Icon className="h-3 w-3 mr-1" />
      {config.label}
    </Badge>
  );
}
