import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Switch } from "../components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import { ScrollArea } from "../components/ui/scroll-area";
import { Progress } from "../components/ui/progress";
import { Badge } from "../components/ui/badge";
import { StatusBadge } from "../components/shared/StatusBadge";
import { ModalityBadge } from "../components/shared/ModalityBadge";
import { Upload, Play, FolderOpen, CheckCircle2, AlertCircle } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";

type IngestionStage =
  | "parsing"
  | "node_generation"
  | "embedding"
  | "writing_qdrant"
  | "writing_index";

export function IngestionPage() {
  const [isRunning, setIsRunning] = useState(false);
  const [hasCompleted, setHasCompleted] = useState(false);
  const [currentStage, setCurrentStage] = useState<IngestionStage | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  const stages: Array<{ id: IngestionStage; label: string }> = [
    { id: "parsing", label: "解析文档" },
    { id: "node_generation", label: "生成多模态节点" },
    { id: "embedding", label: "生成向量" },
    { id: "writing_qdrant", label: "写入 Qdrant" },
    { id: "writing_index", label: "写入本地索引" },
  ];

  const mockNodes = [
    {
      id: "bad448:text:7",
      modality: "text" as const,
      page: 1,
      chunk_index: 7,
      parser: "MinerU",
      excerpt: "We denote Φ0 = 4π²a² nI0 / c，其中包含磁通量与场强推导。",
    },
    {
      id: "bad448:table:0",
      modality: "table" as const,
      page: 3,
      chunk_index: 0,
      parser: "MinerU",
      excerpt: "| Q | 曲线样式 | 说明 |",
    },
    {
      id: "bad448:formula:14",
      modality: "formula" as const,
      page: 1,
      chunk_index: 14,
      parser: "MinerU",
      excerpt: "\\Phi_0 = \\frac{4\\pi^2a^2}{c}nI_0",
    },
    {
      id: "bad448:image:2",
      modality: "image" as const,
      page: 3,
      chunk_index: 2,
      parser: "MinerU",
      excerpt: "Figure 2：不同 Q 值下 Σtot(τ) 的曲线图。",
    },
  ];

  const handleRun = () => {
    setIsRunning(true);
    setHasCompleted(false);
    setCurrentStage(null);
    stages.forEach((stage, index) => {
      setTimeout(() => {
        setCurrentStage(stage.id);
        if (index === stages.length - 1) {
          setTimeout(() => {
            setIsRunning(false);
            setHasCompleted(true);
          }, 1000);
        }
      }, index * 1000);
    });
  };

  return (
    <div className="h-full flex">
      <div className="w-96 border-r bg-card p-6 space-y-6 overflow-auto">
        <div>
          <h2 className="text-base font-semibold mb-4">文档来源</h2>
          <div
            className={`border-2 border-dashed rounded p-6 text-center transition-colors ${
              isDragging
                ? "border-primary bg-primary/5"
                : "border-muted-foreground/25 hover:border-muted-foreground/50"
            }`}
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setIsDragging(false);
            }}
          >
            <Upload className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
            <div className="text-xs">
              <Button variant="link" size="sm" className="p-0 h-auto">
                点击上传
              </Button>
              <span className="text-muted-foreground"> 或拖拽文件到这里</span>
            </div>
            <p className="text-xs text-muted-foreground mt-2">
              支持 PDF、Word、HTML、图片与 Markdown
            </p>
          </div>

          <div className="mt-4">
            <Label htmlFor="directory" className="text-xs">
              或指定本地目录
            </Label>
            <div className="flex gap-2 mt-1.5">
              <Input
                id="directory"
                placeholder="data/demo_docs"
                className="h-8 text-xs"
              />
              <Button size="sm" variant="outline" className="h-8">
                <FolderOpen className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        </div>

        <div className="space-y-4">
          <h2 className="text-base font-semibold">解析配置</h2>

          <div className="space-y-3">
            <div>
              <Label htmlFor="engine" className="text-xs">
                入库引擎
              </Label>
              <Select defaultValue="multimodal">
                <SelectTrigger id="engine" className="h-8 mt-1.5 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="legacy">legacy 文本链路</SelectItem>
                  <SelectItem value="multimodal">multimodal 多模态链路</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div>
              <Label htmlFor="parser" className="text-xs">
                PDF 解析器
              </Label>
              <Select defaultValue="mineru" disabled>
                <SelectTrigger id="parser" className="h-8 mt-1.5 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="mineru">MinerU</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2.5">
              <div className="flex items-center justify-between">
                <Label htmlFor="ocr" className="text-xs">
                  OCR 识别
                </Label>
                <Switch id="ocr" defaultChecked />
              </div>
              <div className="flex items-center justify-between">
                <Label htmlFor="tables" className="text-xs">
                  表格解析
                </Label>
                <Switch id="tables" defaultChecked />
              </div>
              <div className="flex items-center justify-between">
                <Label htmlFor="formulas" className="text-xs">
                  公式抽取
                </Label>
                <Switch id="formulas" defaultChecked />
              </div>
              <div className="flex items-center justify-between">
                <Label htmlFor="images" className="text-xs">
                  图片 Caption
                </Label>
                <Switch id="images" defaultChecked />
              </div>
              <div className="flex items-center justify-between">
                <Label htmlFor="write_qdrant" className="text-xs">
                  写入 Qdrant
                </Label>
                <Switch id="write_qdrant" defaultChecked />
              </div>
            </div>
          </div>
        </div>

        <Button onClick={handleRun} disabled={isRunning} className="w-full">
          <Play className="h-4 w-4 mr-2" />
          开始入库
        </Button>
      </div>

      <div className="flex-1 flex flex-col">
        <div className="p-6 border-b">
          <h1 className="text-2xl font-semibold">文档入库</h1>
          <p className="text-sm text-muted-foreground mt-1">
            将文档解析为 text、table、formula、image 节点，并写入检索索引
          </p>
        </div>

        <ScrollArea className="flex-1">
          <div className="p-6 space-y-6">
            {(isRunning || hasCompleted) && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">入库进度</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  {stages.map((stage) => {
                    const isCurrent = currentStage === stage.id;
                    const currentIndex = stages.findIndex((s) => s.id === currentStage);
                    const stageIndex = stages.findIndex((s) => s.id === stage.id);
                    const isPast = stageIndex < currentIndex || hasCompleted;
                    const status = isPast ? "success" : isCurrent && isRunning ? "running" : "pending";

                    return (
                      <div key={stage.id} className="flex items-center gap-3">
                        <div className="w-40 text-sm text-muted-foreground">
                          {stage.label}
                        </div>
                        <div className="flex-1 flex items-center gap-2">
                          {isCurrent && isRunning && (
                            <Progress value={65} className="flex-1" />
                          )}
                          {isPast && (
                            <div className="flex-1 h-2 bg-green-100 rounded-full" />
                          )}
                          {!isCurrent && !isPast && (
                            <div className="flex-1 h-2 bg-gray-100 rounded-full" />
                          )}
                          <StatusBadge status={status} />
                        </div>
                      </div>
                    );
                  })}
                </CardContent>
              </Card>
            )}

            {hasCompleted && (
              <>
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm">入库摘要</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="grid grid-cols-3 gap-4">
                      <div>
                        <div className="text-xs text-muted-foreground mb-1">
                          节点总数
                        </div>
                        <div className="text-2xl font-semibold">170</div>
                      </div>
                      <div>
                        <div className="text-xs text-muted-foreground mb-1">
                          已生成向量
                        </div>
                        <div className="text-2xl font-semibold">208</div>
                      </div>
                      <div>
                        <div className="text-xs text-muted-foreground mb-1">
                          Upsert 点数
                        </div>
                        <div className="text-2xl font-semibold">208</div>
                      </div>
                    </div>

                    <div className="mt-4 pt-4 border-t space-y-2">
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-muted-foreground">文本节点：</span>
                        <Badge variant="secondary">42</Badge>
                      </div>
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-muted-foreground">表格节点：</span>
                        <Badge variant="secondary">63</Badge>
                      </div>
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-muted-foreground">公式节点：</span>
                        <Badge variant="secondary">60</Badge>
                      </div>
                      <div className="flex items-center justify-between text-xs">
                        <span className="text-muted-foreground">图片节点：</span>
                        <Badge variant="secondary">5</Badge>
                      </div>
                    </div>

                    <div className="mt-4 pt-4 border-t">
                      <div className="flex items-center gap-2 text-xs">
                        <CheckCircle2 className="h-4 w-4 text-green-600" />
                        <span>未发现失败文件</span>
                      </div>
                      <div className="flex items-center gap-2 text-xs mt-2">
                        <AlertCircle className="h-4 w-4 text-yellow-600" />
                        <span>当前为前端模拟结果，真实入库接口接入后显示运行提示</span>
                      </div>
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm">节点预览</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead className="w-36">节点 ID</TableHead>
                          <TableHead className="w-24">模态</TableHead>
                          <TableHead className="w-16">页码</TableHead>
                          <TableHead className="w-24">解析器</TableHead>
                          <TableHead>内容摘录</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {mockNodes.map((node) => (
                          <TableRow key={node.id}>
                            <TableCell className="font-mono text-xs">
                              {node.id}
                            </TableCell>
                            <TableCell>
                              <ModalityBadge modality={node.modality} />
                            </TableCell>
                            <TableCell className="text-xs">{node.page}</TableCell>
                            <TableCell>
                              <Badge variant="secondary" className="text-xs">
                                {node.parser}
                              </Badge>
                            </TableCell>
                            <TableCell className="text-xs text-muted-foreground line-clamp-1">
                              {node.excerpt}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </CardContent>
                </Card>
              </>
            )}

            {!isRunning && !hasCompleted && (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <Upload className="h-12 w-12 text-muted-foreground mb-4" />
                <p className="text-sm text-muted-foreground">
                  选择文档与解析配置后，点击“开始入库”预览处理流程
                </p>
              </div>
            )}
          </div>
        </ScrollArea>
      </div>
    </div>
  );
}
