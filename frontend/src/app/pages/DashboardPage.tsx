import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { StatusBadge } from "../components/shared/StatusBadge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";

const documentStats = [
  {
    name: "2412.16030_Non-stationary_Aharonov-Bohm_effect.pdf",
    type: "PDF",
    chunks: 62,
    text: 18,
    table: 5,
    formula: 22,
    image: 3,
    status: "success" as const,
  },
  {
    name: "2411.15169_Exact_solution_of_the_Heat_Equation.pdf",
    type: "PDF",
    chunks: 36,
    text: 8,
    table: 4,
    formula: 18,
    image: 0,
    status: "success" as const,
  },
  {
    name: "2409.18430_Energy_Levels_and_Transition_Rates.pdf",
    type: "PDF",
    chunks: 34,
    text: 7,
    table: 6,
    formula: 14,
    image: 1,
    status: "success" as const,
  },
  {
    name: "测试文档.pdf",
    type: "PDF",
    chunks: 28,
    text: 3,
    table: 18,
    formula: 2,
    image: 0,
    status: "success" as const,
  },
  {
    name: "测试文档2.pdf",
    type: "PDF",
    chunks: 21,
    text: 3,
    table: 14,
    formula: 1,
    image: 0,
    status: "success" as const,
  },
  {
    name: "Linux常用命令整理/测试表格.md",
    type: "Markdown",
    chunks: 25,
    text: 2,
    table: 16,
    formula: 3,
    image: 0,
    status: "warning" as const,
  },
  {
    name: "联想截图_20260129233900.png",
    type: "图片",
    chunks: 2,
    text: 1,
    table: 0,
    formula: 0,
    image: 1,
    status: "success" as const,
  },
];

export function DashboardPage() {
  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">文件切块统计</h1>
        <p className="text-sm text-muted-foreground mt-1">
          按文件查看入库后切出的 chunk 与多模态节点数量
        </p>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-base">每个文件的切块情况</CardTitle>
              <p className="text-xs text-muted-foreground mt-1">
                用于检查 PDF、Markdown、图片等文件在解析后是否产生了预期的文本、表格、公式和图片节点
              </p>
            </div>
            <Badge variant="outline" className="font-mono">
              agentic_rag_docs
            </Badge>
          </div>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>文件</TableHead>
                <TableHead className="w-24">类型</TableHead>
                <TableHead className="w-24 text-right">Chunk 数</TableHead>
                <TableHead className="w-20 text-right">文本</TableHead>
                <TableHead className="w-20 text-right">表格</TableHead>
                <TableHead className="w-20 text-right">公式</TableHead>
                <TableHead className="w-20 text-right">图片</TableHead>
                <TableHead className="w-24 text-right">节点总数</TableHead>
                <TableHead className="w-24">状态</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {documentStats.map((doc) => {
                const nodeTotal = doc.text + doc.table + doc.formula + doc.image;

                return (
                  <TableRow key={doc.name}>
                    <TableCell>
                      <div className="max-w-[560px] truncate font-mono text-xs" title={doc.name}>
                        {doc.name}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary" className="text-xs">
                        {doc.type}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs font-medium">
                      {doc.chunks}
                    </TableCell>
                    <NumberCell value={doc.text} tone="text-blue-700" />
                    <NumberCell value={doc.table} tone="text-green-700" />
                    <NumberCell value={doc.formula} tone="text-purple-700" />
                    <NumberCell value={doc.image} tone="text-orange-700" />
                    <TableCell className="text-right font-mono text-xs">
                      {nodeTotal}
                    </TableCell>
                    <TableCell>
                      <StatusBadge
                        status={doc.status}
                        label={doc.status === "success" ? "正常" : "需复核"}
                      />
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

function NumberCell({ value, tone }: { value: number; tone: string }) {
  return (
    <TableCell className={`text-right font-mono text-xs ${value ? tone : "text-muted-foreground"}`}>
      {value}
    </TableCell>
  );
}
