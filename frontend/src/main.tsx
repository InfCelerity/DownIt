import React from 'react';
import ReactDOM from 'react-dom/client';
import { FluentProvider } from '@fluentui/react-components';
import App from './App';
import { downitTheme } from './theme';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <FluentProvider theme={downitTheme} style={{ minHeight: '100vh' }}>
      <App />
    </FluentProvider>
  </React.StrictMode>,
);
