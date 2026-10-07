import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import { App } from "./App";
import "./styles.css";

// 不用 StrictMode：它在開發模式會讓 effect 跑兩次，埋點會重複送出，反而誤導「事件怎麼被送出」的示範
createRoot(document.getElementById("root")!).render(
  <BrowserRouter>
    <App />
  </BrowserRouter>,
);
