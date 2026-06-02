import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { StatusBadge } from "../components/shared/StatusBadge";
import { ScrollArea } from "../components/ui/scroll-area";
import { Play, Upload } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";

export function EvaluationPage() {
  const mockMetrics = [
    { name: "Hit Rate", value: "0.87" },
    { name: "MRR", value: "0.76" },
    { name: "Precision@3", value: "0.82" },
    { name: "Recall@10", value: "0.91" },
    { name: "nDCG@10", value: "0.84" },
    { name: "MAP", value: "0.79" },
  ];

  const mockCases = [
    {
      id: "text_taskgraph_intro",
      question: "这个项目支持哪些检索过滤能力？",
      status: "pass" as const,
      citations: 3,
    },
    {
      id: "table_qa",
      question: "表格问答是否可以追溯到原始 chunk？",
      status: "pass" as const,
      citations: 5,
    },
    {
      id: "formula_evidence_gate",
      question: "公式节点是否会被证据门控误判？",
      status: "fail" as const,
      citations: 1,
    },
  ];

  return (
    <div className="h-full flex flex-col">
      <div className="p-6 border-b">
        <h1 className="text-2xl font-semibold">评测</h1>
        <p className="text-sm text-muted-foreground mt-1">
          运行离线或 live eval，观察 RAG 输出、引用和检索指标
        </p>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-6 space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">运行评测</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex gap-2">
                <Button variant="outline" className="flex-1">
                  <Upload className="h-4 w-4 mr-2" />
                  上传测试集 JSONL
                </Button>
                <Button>
                  <Play className="h-4 w-4 mr-2" />
                  开始评测
                </Button>
              </div>

              <div className="flex gap-2">
                <Button variant="outline" size="sm" className="flex-1">
                  离线模式
                </Button>
                <Button variant="outline" size="sm" className="flex-1">
                  实时模式
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">指标</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-3 gap-4">
                {mockMetrics.map((metric) => (
                  <div key={metric.name} className="text-center">
                    <div className="text-2xl font-semibold">{metric.value}</div>
                    <div className="text-xs text-muted-foreground mt-1">
                      {metric.name}
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">评测用例</CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-44">用例 ID</TableHead>
                    <TableHead>问题</TableHead>
                    <TableHead className="w-20">引用数</TableHead>
                    <TableHead className="w-24">状态</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {mockCases.map((c) => (
                    <TableRow key={c.id}>
                      <TableCell className="font-mono text-xs">{c.id}</TableCell>
                      <TableCell className="text-sm">{c.question}</TableCell>
                      <TableCell>
                        <Badge variant="secondary">{c.citations}</Badge>
                      </TableCell>
                      <TableCell>
                        <StatusBadge
                          status={c.status === "pass" ? "success" : "error"}
                          label={c.status === "pass" ? "通过" : "失败"}
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      </ScrollArea>
    </div>
  );
}
