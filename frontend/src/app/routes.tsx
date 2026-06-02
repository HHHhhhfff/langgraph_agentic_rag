import { createBrowserRouter } from "react-router";
import { RootLayout } from "./layouts/RootLayout";
import { DashboardPage } from "./pages/DashboardPage";
import { IngestionPage } from "./pages/IngestionPage";
import { IndexPage } from "./pages/IndexPage";
import { QueryPage } from "./pages/QueryPage";
import { RetrievalPage } from "./pages/RetrievalPage";
import { EvaluationPage } from "./pages/EvaluationPage";
import { SettingsPage } from "./pages/SettingsPage";

export const router = createBrowserRouter([
  {
    path: "/",
    Component: RootLayout,
    children: [
      { index: true, Component: DashboardPage },
      { path: "ingestion", Component: IngestionPage },
      { path: "index", Component: IndexPage },
      { path: "query", Component: QueryPage },
      { path: "retrieval", Component: RetrievalPage },
      { path: "evaluation", Component: EvaluationPage },
      { path: "settings", Component: SettingsPage },
    ],
  },
]);
