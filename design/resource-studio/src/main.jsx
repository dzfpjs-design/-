import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App.jsx";
import RuntimeSetup from "./RuntimeSetup.jsx";
import {enableLocalTransfer} from './local-transfer';
import "./styles.css";

enableLocalTransfer();
createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    {location.pathname.startsWith('/setup')?<RuntimeSetup/>:<App/>}
  </React.StrictMode>,
);
