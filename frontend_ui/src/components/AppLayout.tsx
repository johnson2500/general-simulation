import { NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  Label,
  Masthead,
  MastheadBrand,
  MastheadContent,
  MastheadLogo,
  MastheadMain,
  MastheadToggle,
  Nav,
  NavItem,
  NavList,
  Page,
  PageSidebar,
  PageSidebarBody,
  PageToggleButton,
  SkipToContent,
  Title,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from '@patternfly/react-core'
import BarsIcon from '@patternfly/react-icons/dist/esm/icons/bars-icon'
import { useHealthPoll } from '../hooks/useHealthPoll'

const NAV: { to: string; label: string; end?: boolean }[] = [
  { to: '/', label: 'Overview', end: true },
  { to: '/entities', label: 'Entities' },
  { to: '/map', label: 'Supply chain map' },
  { to: '/graph', label: 'Graph' },
  { to: '/scenarios', label: 'Scenarios' },
  { to: '/query', label: 'Query' },
]

function healthColor(
  status: string | undefined,
): 'green' | 'orange' | 'red' | 'grey' {
  if (status === 'ok') return 'green'
  if (status === 'degraded') return 'orange'
  if (status === 'error') return 'red'
  return 'grey'
}

export function AppLayout() {
  const location = useLocation()
  const health = useHealthPoll()

  const masthead = (
    <Masthead>
      <MastheadMain>
        <MastheadToggle>
          <PageToggleButton
            variant="plain"
            aria-label="Global navigation"
            id="nav-toggle"
          >
            <BarsIcon />
          </PageToggleButton>
        </MastheadToggle>
        <MastheadBrand>
          <MastheadLogo href="/" component="a">
            <Title headingLevel="h1" size="lg">
              Simulation Console
            </Title>
          </MastheadLogo>
        </MastheadBrand>
      </MastheadMain>
      <MastheadContent>
        <Toolbar id="masthead-toolbar" isFullHeight>
          <ToolbarContent>
            <ToolbarItem align={{ default: 'alignEnd' }}>
              <Label color={healthColor(health?.status)}>
                API: {health?.status ?? 'checking…'}
                {health?.db ? ` · DB ${health.db}` : ''}
              </Label>
            </ToolbarItem>
          </ToolbarContent>
        </Toolbar>
      </MastheadContent>
    </Masthead>
  )

  const pageNav = (
    <Nav aria-label="Console">
      <NavList>
        {NAV.map((item) => {
          const active = item.end
            ? location.pathname === item.to
            : location.pathname.startsWith(item.to)
          return (
            <NavItem key={item.to} itemId={item.to} isActive={active}>
              <NavLink to={item.to} end={item.end}>
                {item.label}
              </NavLink>
            </NavItem>
          )
        })}
      </NavList>
    </Nav>
  )

  const sidebar = (
    <PageSidebar>
      <PageSidebarBody>{pageNav}</PageSidebarBody>
    </PageSidebar>
  )

  return (
    <Page
      masthead={masthead}
      sidebar={sidebar}
      isManagedSidebar
      defaultManagedSidebarIsOpen
      skipToContent={
        <SkipToContent href="#main-content">Skip to content</SkipToContent>
      }
      mainContainerId="main-content"
    >
      <Outlet />
    </Page>
  )
}
