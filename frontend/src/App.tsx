import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Accordion,
  AccordionHeader,
  AccordionItem,
  AccordionPanel,
  Badge,
  Button,
  Combobox,
  Divider,
  Dropdown,
  Input,
  MessageBar,
  MessageBarBody,
  Option,
  ProgressBar,
  Spinner,
  Switch,
  Text,
  Tooltip,
  makeStyles,
  mergeClasses,
  tokens,
} from '@fluentui/react-components';
import {
  ArrowClockwise20Regular,
  ArrowDownload24Regular,
  FolderOpen16Regular,
  Search20Regular,
} from '@fluentui/react-icons';
import {
  InstallRequest,
  JobEvent,
  LoaderKind,
  LoaderVersion,
  StatePayload,
  VersionEntry,
  getJob,
  getLoaderVersions,
  getState,
  getVersions,
  pickDirectory,
  startInstall,
} from './api';

type VersionPhase = 'release' | 'snapshot' | 'pre' | 'rc' | 'old_beta' | 'old_alpha' | 'all';

const PHASE_META: Record<string, { label: string; color: 'warning' | 'informative' | 'severe' | 'subtle' }> = {
  snapshot: { label: '快照', color: 'warning' },
  pre: { label: '预发布', color: 'informative' },
  rc: { label: '候选版', color: 'severe' },
  old_beta: { label: 'Beta', color: 'subtle' },
  old_alpha: { label: 'Alpha', color: 'subtle' },
};

const PHASE_LABELS: Record<VersionPhase, string> = {
  release: '正式版',
  snapshot: '快照',
  pre: '预发布',
  rc: '候选版',
  old_beta: 'Beta',
  old_alpha: 'Alpha',
  all: '全部',
};

const LOADERS: Array<{ value: LoaderKind; label: string; hint: string }> = [
  { value: 'none', label: '原版', hint: '只装 Mojang 原版' },
  { value: 'fabric', label: 'Fabric', hint: '轻量模组加载器' },
  { value: 'forge', label: 'Forge', hint: '经典模组加载器' },
  { value: 'neoforge', label: 'NeoForge', hint: '1.20.2 起的 Forge 分支' },
  { value: 'optifine', label: 'OptiFine', hint: '光影与性能优化' },
];

const useStyles = makeStyles({
  root: {
    minHeight: '100vh',
    backgroundColor: tokens.colorNeutralBackground2,
    color: tokens.colorNeutralForeground1,
  },
  header: {
    position: 'sticky',
    top: 0,
    zIndex: 10,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: '16px',
    padding: '12px 24px',
    backgroundColor: tokens.colorNeutralBackground1,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
  },
  brand: { display: 'flex', alignItems: 'center', gap: '12px', minWidth: 0 },
  logo: {
    width: '38px',
    height: '38px',
    borderRadius: '10px',
    display: 'grid',
    placeItems: 'center',
    color: '#fff',
    fontSize: '18px',
    fontWeight: 700,
    backgroundImage: `linear-gradient(145deg, ${tokens.colorBrandBackgroundHover}, ${tokens.colorBrandBackgroundPressed})`,
    flexShrink: 0,
  },
  headerMeta: { display: 'flex', alignItems: 'center', gap: '12px', minWidth: 0, flexShrink: 0 },
  dir: {
    display: 'flex',
    alignItems: 'center',
    gap: '6px',
    maxWidth: '400px',
    minWidth: 0,
    color: tokens.colorNeutralForeground3,
    '@media (max-width: 1100px)': { display: 'none' },
  },
  dirText: {
    fontFamily: 'ui-monospace, Consolas, monospace',
    fontSize: '12px',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  main: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) 392px',
    gap: '16px',
    maxWidth: '1320px',
    margin: '0 auto',
    padding: '16px 24px 40px',
    alignItems: 'start',
    '@media (max-width: 1020px)': {
      gridTemplateColumns: 'minmax(0, 1fr)',
      padding: '12px 12px 28px',
    },
  },
  side: { display: 'flex', flexDirection: 'column', gap: '16px' },
  panel: {
    backgroundColor: tokens.colorNeutralBackground1,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    boxShadow: tokens.shadow2,
    overflow: 'hidden',
  },
  panelHead: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: '8px',
    padding: '12px 16px',
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
  },
  titleGroup: { display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 },
  toolbar: {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    padding: '10px 12px',
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
  },
  search: { flex: 1, minWidth: 0 },
  kindSelect: { width: '112px', flexShrink: 0 },
  list: {
    maxHeight: 'clamp(320px, calc(100vh - 250px), 720px)',
    overflowY: 'auto',
    padding: '6px',
    display: 'flex',
    flexDirection: 'column',
    gap: '2px',
    '@media (max-width: 1020px)': { maxHeight: '42vh' },
  },
  row: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) auto',
    alignItems: 'center',
    gap: '8px',
    width: '100%',
    padding: '8px 12px',
    border: '1px solid transparent',
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: 'transparent',
    cursor: 'pointer',
    textAlign: 'left',
    fontFamily: 'inherit',
    color: 'inherit',
    ':hover': { backgroundColor: tokens.colorNeutralBackground1Hover },
    ':focus-visible': { outline: `2px solid ${tokens.colorStrokeFocus2}` },
  },
  rowSelected: {
    backgroundColor: tokens.colorBrandBackground2,
    border: `1px solid ${tokens.colorBrandStroke1}`,
    ':hover': { backgroundColor: tokens.colorBrandBackground2Hover },
  },
  rowLeft: { display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 },
  versionId: {
    fontFamily: 'ui-monospace, Consolas, monospace',
    fontSize: '13px',
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  muted: { color: tokens.colorNeutralForeground3 },
  body: { display: 'flex', flexDirection: 'column', gap: '14px', padding: '16px' },
  field: { display: 'flex', flexDirection: 'column', gap: '6px' },
  label: { color: tokens.colorNeutralForeground3, fontSize: '12px' },
  labelRow: { display: 'flex', alignItems: 'center', gap: '8px' },
  fieldRow: { display: 'flex', alignItems: 'center', gap: '8px' },
  full: { width: '100%' },
  grow: { flex: 1, minWidth: 0 },
  hint: { color: tokens.colorNeutralForeground3, fontSize: '12px' },
  warn: { color: tokens.colorPaletteRedForeground1, fontSize: '12px' },
  progressArea: { padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: '8px' },
  progressMeta: {
    display: 'flex',
    justifyContent: 'space-between',
    gap: '8px',
    fontSize: '12px',
    color: tokens.colorNeutralForeground3,
    fontVariantNumeric: 'tabular-nums',
  },
  log: {
    margin: 0,
    padding: '10px 16px 14px',
    height: '220px',
    overflowY: 'auto',
    backgroundColor: tokens.colorNeutralBackground2,
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`,
    fontFamily: 'ui-monospace, Consolas, monospace',
    fontSize: '11.5px',
    lineHeight: 1.7,
    color: tokens.colorNeutralForeground2,
  },
  logLine: { whiteSpace: 'pre-wrap', wordBreak: 'break-word' },
  logError: { color: tokens.colorPaletteRedForeground1 },
  logOk: { color: tokens.colorPaletteGreenForeground1 },
  footer: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: '8px',
    padding: '0 16px 16px',
    color: tokens.colorNeutralForeground3,
    fontSize: '12px',
  },
});

function humanBytes(value: number): string {
  if (!value) return '—';
  const units = ['B', 'KB', 'MB', 'GB'];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${index === 0 ? size : size.toFixed(1)} ${units[index]}`;
}

export default function App() {
  const styles = useStyles();
  const [state, setState] = useState<StatePayload | null>(null);
  const [versions, setVersions] = useState<VersionEntry[]>([]);
  const [latest, setLatest] = useState<{ release: string; snapshot: string } | null>(null);
  const [loadError, setLoadError] = useState('');
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<VersionPhase>('release');
  const [selected, setSelected] = useState<string | null>(null);

  const [loader, setLoader] = useState<LoaderKind>('none');
  const [loaderVersion, setLoaderVersion] = useState('');
  const [loaderOptions, setLoaderOptions] = useState<LoaderVersion[]>([]);
  const [versionsLoading, setVersionsLoading] = useState(false);
  const [versionsError, setVersionsError] = useState('');
  const [optifineMode, setOptifineMode] = useState<'none' | 'forge'>('none');

  const [mirror, setMirror] = useState('auto');
  const [skipAssets, setSkipAssets] = useState(false);
  const [gameDir, setGameDir] = useState('');
  const [workers, setWorkers] = useState(8);
  const [javaPath, setJavaPath] = useState('');
  const [quiet, setQuiet] = useState(false);
  const [dirHint, setDirHint] = useState('');

  const [status, setStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
  const [progress, setProgress] = useState({ done: 0, total: 0, files: 0, filesTotal: 0 });
  const [logs, setLogs] = useState<string[]>([]);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const sinceRef = useRef(0);
  const logRef = useRef<HTMLDivElement>(null);

  const installed = useMemo(
    () => new Set((state?.installed ?? []).map((item) => item.id)),
    [state?.installed],
  );

  const loadState = useCallback(async () => {
    try {
      const payload = await getState();
      setState(payload);
      setGameDir((previous) => previous || payload.game_dir);
      if (payload.workers) setWorkers(payload.workers);
      if (payload.busy && payload.job) setJobId(payload.job);
    } catch {
      /* 服务刚启动时忽略 */
    }
  }, []);

  const loadVersions = useCallback(async () => {
    try {
      const payload = await getVersions();
      setVersions(payload.versions ?? []);
      setLatest(payload.latest ?? null);
      if (payload.error) setLoadError(payload.error);
    } catch (exc) {
      setLoadError(exc instanceof Error ? exc.message : String(exc));
    }
  }, []);

  useEffect(() => {
    void loadState();
    void loadVersions();
  }, [loadState, loadVersions]);

  // 切换 MC 版本或加载器时，自动拉取该版本可用的加载器版本
  useEffect(() => {
    if (!selected || loader === 'none') {
      setLoaderOptions([]);
      setLoaderVersion('');
      setVersionsError('');
      return;
    }
    let cancelled = false;
    setVersionsLoading(true);
    setVersionsError('');
    const timer = window.setTimeout(async () => {
      try {
        const payload = await getLoaderVersions(loader, selected);
        if (cancelled) return;
        setLoaderOptions(payload.versions ?? []);
        setLoaderVersion('');
        if (payload.error) setVersionsError(payload.error);
      } catch (exc) {
        if (!cancelled) setVersionsError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        if (!cancelled) setVersionsLoading(false);
      }
    }, 260);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [selected, loader]);

  const appendEvents = useCallback((events: JobEvent[]) => {
    const texts: string[] = [];
    events.forEach((event) => {
      if (event.kind === 'log' && event.text) texts.push(event.text);
      if (event.kind === 'progress') {
        setProgress({
          done: event.done ?? 0,
          total: event.total ?? 0,
          files: event.files ?? 0,
          filesTotal: event.files_total ?? 0,
        });
      }
      if (event.kind === 'status' && event.status) {
        setStatus(event.status);
        if (event.error) setError(event.error);
      }
    });
    if (texts.length) {
      setLogs((previous) => [...previous, ...texts].slice(-400));
    }
  }, []);

  useEffect(() => {
    if (!jobId) return;
    let cancelled = false;
    let timer = 0;

    const tick = async () => {
      try {
        const payload = await getJob(jobId, sinceRef.current);
        if (cancelled) return;
        appendEvents(payload.events);
        sinceRef.current = payload.next;
        if (payload.status === 'done' || payload.status === 'error') {
          setStatus(payload.status);
          if (payload.error) setError(payload.error);
          setJobId(null);
          void loadState();
          return;
        }
      } catch {
        /* 网络抖动，继续轮询 */
      }
      if (!cancelled) timer = window.setTimeout(tick, 600);
    };

    void tick();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [jobId, appendEvents, loadState]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs]);

  const visibleVersions = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return versions
      .filter((item) => (kind === 'all' ? true : item.phase === kind))
      .filter((item) => (needle ? item.id.toLowerCase().includes(needle) : true))
      .slice(0, 250);
  }, [versions, kind, query]);

  const percent = useMemo(() => {
    if (progress.total > 0) return Math.min(1, progress.done / progress.total);
    if (progress.filesTotal > 0) return Math.min(1, progress.files / progress.filesTotal);
    return 0;
  }, [progress]);

  const busy = status === 'running';
  const loaderMeta = LOADERS.find((item) => item.value === loader)!;
  const selectedJava = state?.javas.find((item) => item.path === javaPath);
  const javaLabel = selectedJava
    ? `Java ${selectedJava.major} · ${selectedJava.version}`
    : javaPath || '自动选择';

  const browseDir = async () => {
    setDirHint('');
    try {
      const result = await pickDirectory();
      if (result.path) {
        setGameDir(result.path);
      } else {
        setDirHint('当前不是桌面窗口模式，直接输入路径即可');
      }
    } catch {
      setDirHint('无法打开目录选择框，请手动输入路径');
    }
  };

  const start = async () => {
    if (!selected) return;
    setLogs([]);
    setError('');
    setProgress({ done: 0, total: 0, files: 0, filesTotal: 0 });
    setStatus('running');
    sinceRef.current = 0;
    const payload: InstallRequest = {
      version: selected,
      loader,
      loader_version: loaderVersion.trim(),
      optifine_mode: optifineMode,
      skip_assets: skipAssets,
      mirror,
      dir: gameDir.trim(),
      workers,
      java: javaPath,
      quiet,
    };
    try {
      const result = await startInstall(payload);
      setJobId(result.job);
    } catch (exc) {
      setStatus('error');
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  const statusBadge = () => {
    if (status === 'running') {
      return (
        <Badge appearance="tint" color="warning" icon={<Spinner size="extra-tiny" />}>
          进行中
        </Badge>
      );
    }
    if (status === 'done') return <Badge appearance="tint" color="success">完成</Badge>;
    if (status === 'error') return <Badge appearance="tint" color="danger">失败</Badge>;
    return <Badge appearance="tint">空闲</Badge>;
  };

  return (
    <div className={styles.root}>
      <header className={styles.header}>
        <div className={styles.brand}>
          <div className={styles.logo}>▣</div>
          <div>
            <Text weight="semibold" size={400}>
              DownIt
            </Text>
            <div>
              <Text size={200} className={styles.muted}>
                Minecraft 下载器 · Mojang CDN / Fabric / Forge / NeoForge / OptiFine
              </Text>
            </div>
          </div>
        </div>
        <div className={styles.headerMeta}>
          <Tooltip content={gameDir || state?.game_dir || ''} relationship="description">
            <div className={styles.dir}>
              <FolderOpen16Regular />
              <span className={styles.dirText}>{gameDir || state?.game_dir || '加载中…'}</span>
            </div>
          </Tooltip>
          <Badge appearance="tint" color="success" style={{ whiteSpace: 'nowrap' }}>
            本地服务
          </Badge>
        </div>
      </header>

      <main className={styles.main}>
        <section className={styles.panel}>
          <div className={styles.panelHead}>
            <div className={styles.titleGroup}>
              <Text weight="semibold">版本列表</Text>
              {latest && <Badge appearance="tint" color="brand">最新 {latest.release}</Badge>}
            </div>
            <Tooltip content="刷新版本清单" relationship="description">
              <Button
                appearance="subtle"
                icon={<ArrowClockwise20Regular />}
                onClick={() => void loadVersions()}
                aria-label="刷新"
              />
            </Tooltip>
          </div>

          <div className={styles.toolbar}>
            <Input
              className={styles.search}
              contentBefore={<Search20Regular />}
              placeholder="搜索版本号"
              value={query}
              onChange={(_, data) => setQuery(data.value)}
            />
            <Dropdown
              className={styles.kindSelect}
              value={PHASE_LABELS[kind]}
              selectedOptions={[kind]}
              onOptionSelect={(_, data) => setKind((data.optionValue as VersionPhase) ?? 'release')}
            >
              <Option value="release">正式版</Option>
              <Option value="snapshot">快照</Option>
              <Option value="pre">预发布</Option>
              <Option value="rc">候选版</Option>
              <Option value="all">全部</Option>
            </Dropdown>
          </div>

          <div className={styles.list}>
            {visibleVersions.length === 0 && (
              <Text className={styles.muted} style={{ padding: '24px', textAlign: 'center' }}>
                {loadError || '没有匹配的版本'}
              </Text>
            )}
            {visibleVersions.map((item) => (
              <button
                key={item.id}
                type="button"
                className={mergeClasses(
                  styles.row,
                  selected === item.id ? styles.rowSelected : undefined,
                )}
                onClick={() => setSelected(item.id)}
              >
                <span className={styles.rowLeft}>
                  <span className={styles.versionId}>{item.id}</span>
                  {PHASE_META[item.phase] && (
                    <Badge appearance="tint" color={PHASE_META[item.phase].color} size="small">
                      {PHASE_META[item.phase].label}
                    </Badge>
                  )}
                  {installed.has(item.id) && (
                    <Badge appearance="tint" color="success" size="small">
                      已安装
                    </Badge>
                  )}
                </span>
                <Text size={200} className={styles.muted}>
                  {item.releaseTime}
                </Text>
              </button>
            ))}
          </div>
        </section>

        <aside className={styles.side}>
          <section className={styles.panel}>
            <div className={styles.panelHead}>
              <Text weight="semibold">安装</Text>
              <Badge appearance="outline" color={selected ? 'brand' : 'subtle'}>
                {selected ?? '未选择版本'}
              </Badge>
            </div>
            <div className={styles.body}>
              <div className={styles.field}>
                <Text className={styles.label}>模组加载器</Text>
                <Dropdown
                  className={styles.full}
                  value={loaderMeta.label}
                  selectedOptions={[loader]}
                  onOptionSelect={(_, data) => setLoader((data.optionValue as LoaderKind) ?? 'none')}
                >
                  {LOADERS.map((item) => (
                    <Option key={item.value} value={item.value} text={item.label}>
                      {item.label} · {item.hint}
                    </Option>
                  ))}
                </Dropdown>
              </div>

              {loader !== 'none' && (
                <div className={styles.field}>
                  <div className={styles.labelRow}>
                    <Text className={styles.label}>
                      {loader === 'optifine' ? 'OptiFine 版本' : '加载器版本'}
                    </Text>
                    {versionsLoading && <Spinner size="extra-tiny" />}
                    {!versionsLoading && loaderOptions.length > 0 && (
                      <Text className={styles.muted} size={100}>
                        {loaderOptions.length} 个可选
                      </Text>
                    )}
                  </div>
                  <Combobox
                    className={styles.full}
                    value={loaderVersion}
                    selectedOptions={loaderVersion ? [loaderVersion] : []}
                    placeholder={
                      !selected
                        ? '先选择左侧版本'
                        : versionsLoading
                          ? '正在获取可用版本…'
                          : '自动（推荐）'
                    }
                    disabled={!selected}
                    onChange={(event) => setLoaderVersion(event.target.value)}
                    onOptionSelect={(_, data) => setLoaderVersion(data.optionValue ?? '')}
                  >
                    <Option value="" text="自动（推荐）">
                      自动（推荐）
                    </Option>
                    {loaderOptions.map((item) => (
                      <Option key={item.value} value={item.value} text={item.label}>
                        {item.note ? `${item.label} · ${item.note}` : item.label}
                      </Option>
                    ))}
                  </Combobox>
                  {versionsError && (
                    <Text className={styles.warn}>
                      版本列表获取失败，可直接输入版本号：{versionsError}
                    </Text>
                  )}
                  {!versionsError && selected && !versionsLoading && loaderOptions.length === 0 && (
                    <Text className={styles.muted} size={100}>
                      没有查到可用版本，可手动输入
                    </Text>
                  )}
                </div>
              )}

              {loader === 'optifine' && (
                <div className={styles.field}>
                  <Text className={styles.label}>安装方式</Text>
                  <Dropdown
                    className={styles.full}
                    value={optifineMode === 'none' ? '独立版本（推荐）' : '作为 Forge 模组'}
                    selectedOptions={[optifineMode]}
                    onOptionSelect={(_, data) =>
                      setOptifineMode((data.optionValue as 'none' | 'forge') ?? 'none')
                    }
                  >
                    <Option value="none">独立版本（推荐）</Option>
                    <Option value="forge">作为 Forge 模组</Option>
                  </Dropdown>
                  {optifineMode === 'forge' && (
                    <Text className={styles.hint}>会先安装 Forge，再把 OptiFine 放进 mods/</Text>
                  )}
                </div>
              )}

              <div className={styles.field}>
                <Text className={styles.label}>下载源</Text>
                <Dropdown
                  className={styles.full}
                  value={
                    mirror === 'auto'
                      ? '自动测速'
                      : mirror === 'official'
                        ? 'Mojang 官方'
                        : 'BMCLAPI 镜像'
                  }
                  selectedOptions={[mirror]}
                  onOptionSelect={(_, data) => setMirror(data.optionValue ?? 'auto')}
                >
                  <Option value="auto">自动测速</Option>
                  <Option value="official">Mojang 官方</Option>
                  <Option value="bmclapi">BMCLAPI 国内镜像</Option>
                </Dropdown>
              </div>

              <Switch
                checked={skipAssets}
                onChange={(_, data) => setSkipAssets(data.checked)}
                label="跳过资源文件（更快，但游戏内无声音）"
              />

              <Accordion collapsible>
                <AccordionItem value="advanced">
                  <AccordionHeader size="small">高级选项</AccordionHeader>
                  <AccordionPanel>
                    <div className={styles.body} style={{ padding: '8px 0 0' }}>
                      <div className={styles.field}>
                        <Text className={styles.label}>游戏目录</Text>
                        <div className={styles.fieldRow}>
                          <Input
                            className={styles.grow}
                            value={gameDir}
                            onChange={(_, data) => setGameDir(data.value)}
                            placeholder="默认使用官方 .minecraft"
                          />
                          <Button
                            icon={<FolderOpen16Regular />}
                            onClick={() => void browseDir()}
                            title="选择目录"
                          >
                            浏览
                          </Button>
                        </div>
                        {dirHint && <Text className={styles.hint}>{dirHint}</Text>}
                      </div>

                      <div className={styles.field}>
                        <Text className={styles.label}>并发下载数</Text>
                        <Input
                          className={styles.full}
                          type="number"
                          min={1}
                          max={32}
                          value={String(workers)}
                          onChange={(_, data) => {
                            const value = Number(data.value);
                            setWorkers(Number.isFinite(value) && value > 0 ? value : 1);
                          }}
                        />
                      </div>

                      <div className={styles.field}>
                        <Text className={styles.label}>
                          Java 运行时（Forge / NeoForge / OptiFine 需要）
                        </Text>
                        <Dropdown
                          className={styles.full}
                          value={javaLabel}
                          selectedOptions={[javaPath || 'auto']}
                          onOptionSelect={(_, data) =>
                            setJavaPath(
                              !data.optionValue || data.optionValue === 'auto'
                                ? ''
                                : data.optionValue,
                            )
                          }
                        >
                          <Option value="auto" text="自动选择">
                            自动选择
                          </Option>
                          {(state?.javas ?? []).map((item) => (
                            <Option
                              key={item.path}
                              value={item.path}
                              text={`Java ${item.major} · ${item.version}`}
                            >
                              Java {item.major} · {item.version} · {item.path}
                            </Option>
                          ))}
                        </Dropdown>
                      </div>

                      <Switch
                        checked={quiet}
                        onChange={(_, data) => setQuiet(data.checked)}
                        label="精简日志（只显示关键步骤）"
                      />
                    </div>
                  </AccordionPanel>
                </AccordionItem>
              </Accordion>

              <Button
                appearance="primary"
                size="large"
                icon={<ArrowDownload24Regular />}
                disabled={!selected || busy}
                onClick={() => void start()}
              >
                {busy ? '正在安装…' : '开始下载'}
              </Button>

              <Text className={styles.hint}>
                安装到 {gameDir || state?.game_dir || '…'}，已装过的文件会自动跳过
              </Text>
            </div>
          </section>

          <section className={styles.panel}>
            <div className={styles.panelHead}>
              <Text weight="semibold">进度</Text>
              {statusBadge()}
            </div>
            <div className={styles.progressArea}>
              <ProgressBar value={percent} thickness="large" />
              <div className={styles.progressMeta}>
                <span>{(percent * 100).toFixed(0)}%</span>
                <span>
                  {humanBytes(progress.done)}
                  {progress.total > 0 ? ` / ${humanBytes(progress.total)}` : ''}
                </span>
                <span>
                  {progress.filesTotal > 0 ? `${progress.files} / ${progress.filesTotal} 文件` : ''}
                </span>
              </div>
            </div>
            <div className={styles.log} ref={logRef}>
              {logs.length === 0 ? (
                <Text size={200} className={styles.muted}>
                  等待任务…
                </Text>
              ) : (
                logs.map((line, index) => (
                  <div
                    key={`${index}-${line.slice(0, 12)}`}
                    className={mergeClasses(
                      styles.logLine,
                      /^×|错误|失败/.test(line) ? styles.logError : undefined,
                      /^完成|^✓/.test(line) ? styles.logOk : undefined,
                    )}
                  >
                    {line}
                  </div>
                ))
              )}
            </div>
            {error && (
              <div style={{ padding: '0 16px 16px' }}>
                <MessageBar intent="error">
                  <MessageBarBody>{error}</MessageBarBody>
                </MessageBar>
              </div>
            )}
          </section>

          <div className={styles.footer}>
            <Text size={200}>DownIt v{state?.version ?? '1.0.0'}</Text>
            <Divider vertical />
            <Text size={200}>Java 由本机提供 · 不包含登录功能</Text>
          </div>
        </aside>
      </main>
    </div>
  );
}
