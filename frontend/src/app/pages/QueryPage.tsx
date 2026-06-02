import { useState } from "react";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Progress } from "../components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import { ScrollArea } from "../components/ui/scroll-area";
import { Separator } from "../components/ui/separator";
import { ModalityBadge } from "../components/shared/ModalityBadge";
import { StatusBadge } from "../components/shared/StatusBadge";
import {
  Send,
  ChevronRight,
  FileText,
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ExternalLink,
} from "lucide-react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "../components/ui/collapsible";

type QueryStage =
  | "analyze"
  | "route"
  | "retrieve"
  | "gate"
  | "generate"
  | "verify";

interface EvidenceItem {
  id: string;
  source: string;
  title: string;
  page: number;
  chunk_index: number;
  score: number;
  modality: "text" | "table" | "formula" | "image";
  channel: string;
  parser: string;
  text: string;
  score_vector?: number;
  score_bm25?: number;
  score_rrf?: number;
  rerank_score?: number;
}

export function QueryPage() {
  const [query, setQuery] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [hasResult, setHasResult] = useState(false);
  const [currentStage, setCurrentStage] = useState<QueryStage | null>(null);
  const [expandedEvidence, setExpandedEvidence] = useState<string | null>(null);

  const stages: Array<{ id: QueryStage; label: string }> = [
    { id: "analyze", label: "问题分析" },
    { id: "route", label: "路由决策" },
    { id: "retrieve", label: "检索召回" },
    { id: "gate", label: "证据门控" },
    { id: "generate", label: "生成回答" },
    { id: "verify", label: "引用校验" },
  ];

  const mockEvidence: EvidenceItem[] = [
    {
      id: "e1",
      source: "2412.16030_Non-stationary_Aharonov-Bohm_effect.pdf",
      title: "Non-stationary Aharonov-Bohm effect",
      page: 3,
      chunk_index: 12,
      score: 0.91,
      modality: "text",
      channel: "vector",
      parser: "MinerU",
      text: "Figure 2 描述了 Σtot(τ)=σ(τ)/σ0 随 τ 的变化，其中 Q=1 为实线，Q=2 为虚线，Q=3 为点线。",
      score_vector: 0.86,
      score_bm25: 0.77,
      rerank_score: 0.91,
    },
    {
      id: "e2",
      source: "2412.16030_Non-stationary_Aharonov-Bohm_effect.pdf",
      title: "Non-stationary Aharonov-Bohm effect",
      page: 1,
      chunk_index: 7,
      score: 0.84,
      modality: "formula",
      channel: "relationship",
      parser: "MinerU",
      text: "\\Phi_0 = \\frac{4\\pi^2a^2}{c}nI_0，其中 n 表示螺线管单位长度的匝数。",
      score_vector: 0.74,
      score_rrf: 0.81,
      rerank_score: 0.84,
    },
    {
      id: "e3",
      source: "2412.16030_Non-stationary_Aharonov-Bohm_effect.pdf",
      title: "Non-stationary Aharonov-Bohm effect",
      page: 3,
      chunk_index: 2,
      score: 0.78,
      modality: "image",
      channel: "image",
      parser: "MinerU",
      text: "图像节点包含 Figure 2 的曲线裁剪，caption 中保留了 Q=1、Q=2、Q=3 与线型的对应关系。",
      score_vector: 0.78,
      rerank_score: 0.78,
    },
  ];

  const handleSubmit = () => {
    if (!query.trim()) return;
    setIsRunning(true);
    setHasResult(false);
    setCurrentStage(null);

    stages.forEach((stage, index) => {
      setTimeout(() => {
        setCurrentStage(stage.id);
        if (index === stages.length - 1) {
          setTimeout(() => {
            setIsRunning(false);
            setHasResult(true);
          }, 800);
        }
      }, index * 900);
    });
  };

  const getStageStatus = (stageId: QueryStage) => {
    if (!currentStage) return "pending";
    const currentIndex = stages.findIndex((s) => s.id === currentStage);
    const stageIndex = stages.findIndex((s) => s.id === stageId);
    if (stageIndex < currentIndex) return "success";
    if (stageIndex === currentIndex) return isRunning ? "running" : "success";
    return "pending";
  };

  return (
    <div className="h-full flex">
      <div className="flex-1 flex flex-col">
        <div className="p-6 border-b space-y-4">
          <div>
            <h1 className="text-2xl font-semibold">问答工作台</h1>
            <p className="text-sm text-muted-foreground mt-1">
              提问、查看答案引用，并分析 TaskGraph 的检索证据链
            </p>
          </div>

          <div className="flex gap-2">
            <Input
              placeholder="输入问题，例如：Q=3 对应哪种曲线？n 表示什么？"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
              className="flex-1"
            />
            <Select defaultValue="all">
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部来源</SelectItem>
                <SelectItem value="pdf">仅 PDF</SelectItem>
                <SelectItem value="image">仅图片来源</SelectItem>
              </SelectContent>
            </Select>
            <Select defaultValue="all_modality">
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all_modality">全部模态</SelectItem>
                <SelectItem value="text">文本</SelectItem>
                <SelectItem value="table">表格</SelectItem>
                <SelectItem value="formula">公式</SelectItem>
                <SelectItem value="image">图片</SelectItem>
              </SelectContent>
            </Select>
            <Button onClick={handleSubmit} disabled={isRunning || !query.trim()}>
              <Send className="h-4 w-4 mr-2" />
              查询
            </Button>
          </div>
        </div>

        <ScrollArea className="flex-1">
          <div className="p-6 space-y-6">
            {(isRunning || hasResult) && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm font-medium">
                    TaskGraph 进度
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  {stages.map((stage) => {
                    const status = getStageStatus(stage.id);
                    return (
                      <div key={stage.id} className="flex items-center gap-3">
                        <div className="w-32 text-sm text-muted-foreground">
                          {stage.label}
                        </div>
                        <div className="flex-1 flex items-center gap-2">
                          {status === "running" && (
                            <Progress value={60} className="flex-1" />
                          )}
                          {status === "success" && (
                            <div className="flex-1 h-2 bg-green-100 rounded-full" />
                          )}
                          {status === "pending" && (
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

            {hasResult && (
              <>
                <Card>
                  <CardHeader>
                    <div className="flex items-start justify-between">
                      <div>
                        <CardTitle className="text-base">回答</CardTitle>
                        <p className="text-sm text-muted-foreground mt-1">
                          Q=3 对应哪种曲线？n 表示什么？
                        </p>
                      </div>
                      <StatusBadge status="success" label="证据通过" />
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="text-sm leading-relaxed">
                      在 Figure 2 中，Q=3 对应点线曲线
                      <sup className="text-primary cursor-pointer hover:underline">[1]</sup>
                      。公式中 n 表示螺线管单位长度上的匝数
                      <sup className="text-primary cursor-pointer hover:underline">[2]</sup>
                      。图片裁剪节点也保留了曲线样式与 Q 值的对应关系
                      <sup className="text-primary cursor-pointer hover:underline">[3]</sup>
                      。
                    </div>

                    <Separator />

                    <div className="space-y-2">
                      <div className="text-xs font-medium text-muted-foreground">
                        调试摘要
                      </div>
                      <div className="grid grid-cols-2 gap-2 text-xs">
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">路由：</span>
                          <Badge variant="secondary" className="text-xs">
                            image_first
                          </Badge>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">通道：</span>
                          <span className="font-mono">vector, bm25, image, relationship</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">证据通过：</span>
                          <CheckCircle2 className="h-3.5 w-3.5 text-green-600" />
                        </div>
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">支持分：</span>
                          <span className="font-mono">0.87</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">重试次数：</span>
                          <span className="font-mono">1</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-muted-foreground">引用校验：</span>
                          <CheckCircle2 className="h-3.5 w-3.5 text-green-600" />
                        </div>
                      </div>
                    </div>
                  </CardContent>
                </Card>

                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-medium">
                    召回证据（{mockEvidence.length}）
                  </h3>
                  <Badge variant="outline">使用 3 条引用</Badge>
                </div>
              </>
            )}

            {!isRunning && !hasResult && (
              <div className="flex flex-col items-center justify-center py-16 text-center">
                <FileText className="h-12 w-12 text-muted-foreground mb-4" />
                <p className="text-sm text-muted-foreground">
                  输入问题后开始查询，并在右侧查看证据与引用
                </p>
              </div>
            )}
          </div>
        </ScrollArea>
      </div>

      {hasResult && (
        <div className="w-96 border-l bg-card">
          <ScrollArea className="h-full">
            <div className="p-4 space-y-3">
              <h3 className="text-sm font-semibold">证据与引用</h3>

              {mockEvidence.map((evidence, index) => (
                <Collapsible
                  key={evidence.id}
                  open={expandedEvidence === evidence.id}
                  onOpenChange={(open) =>
                    setExpandedEvidence(open ? evidence.id : null)
                  }
                >
                  <Card>
                    <CardHeader className="pb-3">
                      <div className="space-y-2">
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex items-center gap-2">
                            <Badge
                              variant="outline"
                              className="font-mono text-xs"
                            >
                              [{index + 1}]
                            </Badge>
                            <ModalityBadge modality={evidence.modality} />
                          </div>
                          <div className="text-xs font-mono text-muted-foreground">
                            {evidence.score.toFixed(2)}
                          </div>
                        </div>

                        <div className="space-y-1">
                          <div className="text-xs font-medium flex items-center gap-1">
                            {evidence.title}
                            <ExternalLink className="h-3 w-3 text-muted-foreground" />
                          </div>
                          <div className="flex items-center gap-2 text-xs text-muted-foreground">
                            <span>第 {evidence.page} 页</span>
                            <span>·</span>
                            <span>chunk_{evidence.chunk_index}</span>
                            <span>·</span>
                            <Badge variant="secondary" className="text-xs">
                              {evidence.channel}
                            </Badge>
                          </div>
                        </div>
                      </div>
                    </CardHeader>

                    <CardContent className="space-y-3">
                      <div className="text-xs line-clamp-3 text-muted-foreground">
                        {evidence.text}
                      </div>

                      <CollapsibleTrigger asChild>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="w-full h-7 text-xs"
                        >
                          {expandedEvidence === evidence.id ? (
                            <>
                              <ChevronDown className="h-3 w-3 mr-1" />
                              收起详情
                            </>
                          ) : (
                            <>
                              <ChevronRight className="h-3 w-3 mr-1" />
                              查看详情
                            </>
                          )}
                        </Button>
                      </CollapsibleTrigger>

                      <CollapsibleContent className="space-y-3">
                        <Separator />
                        <div className="space-y-2">
                          <div className="text-xs font-medium">分数组成</div>
                          <div className="space-y-1.5 text-xs">
                            {evidence.score_vector !== undefined && (
                              <div className="flex justify-between">
                                <span className="text-muted-foreground">向量：</span>
                                <span className="font-mono">
                                  {evidence.score_vector.toFixed(3)}
                                </span>
                              </div>
                            )}
                            {evidence.score_bm25 !== undefined && (
                              <div className="flex justify-between">
                                <span className="text-muted-foreground">BM25：</span>
                                <span className="font-mono">
                                  {evidence.score_bm25.toFixed(3)}
                                </span>
                              </div>
                            )}
                            {evidence.score_rrf !== undefined && (
                              <div className="flex justify-between">
                                <span className="text-muted-foreground">RRF：</span>
                                <span className="font-mono">
                                  {evidence.score_rrf.toFixed(3)}
                                </span>
                              </div>
                            )}
                            {evidence.rerank_score !== undefined && (
                              <div className="flex justify-between">
                                <span className="text-muted-foreground">Rerank：</span>
                                <span className="font-mono">
                                  {evidence.rerank_score.toFixed(3)}
                                </span>
                              </div>
                            )}
                          </div>
                        </div>

                        <div className="space-y-2">
                          <div className="text-xs font-medium">元数据</div>
                          <div className="space-y-1 text-xs">
                            <div className="flex justify-between gap-3">
                              <span className="text-muted-foreground">来源：</span>
                              <span className="font-mono text-right">
                                {evidence.source}
                              </span>
                            </div>
                            <div className="flex justify-between">
                              <span className="text-muted-foreground">解析器：</span>
                              <Badge variant="secondary" className="text-xs">
                                {evidence.parser}
                              </Badge>
                            </div>
                          </div>
                        </div>

                        <div className="space-y-2">
                          <div className="text-xs font-medium">完整片段</div>
                          <div className="text-xs text-muted-foreground bg-muted/50 p-2 rounded border">
                            {evidence.text}
                          </div>
                        </div>
                      </CollapsibleContent>
                    </CardContent>
                  </Card>
                </Collapsible>
              ))}

              <Card className="border-yellow-200 bg-yellow-50">
                <CardContent className="pt-4">
                  <div className="flex gap-2">
                    <AlertCircle className="h-4 w-4 text-yellow-600 mt-0.5 flex-shrink-0" />
                    <div className="space-y-1">
                      <div className="text-xs font-medium text-yellow-900">
                        可复核项
                      </div>
                      <div className="text-xs text-yellow-700">
                        本次 query 触发了 1 次局部重检，建议在检索诊断页查看 retry 前后的排名变化。
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>
          </ScrollArea>
        </div>
      )}
    </div>
  );
}
