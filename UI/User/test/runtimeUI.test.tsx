import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import InteractiveDetails from '../src/pages/InteractiveDetails';
import { interactive, type Runtime } from '../src/services/interactive';
import { record } from '../src/services/terminalVerification';

vi.mock('../src/services/interactive', () => ({interactive:{detail:vi.fn(),logs:vi.fn(),runtime:vi.fn(),start:vi.fn(),stop:vi.fn(),connection:vi.fn()}}));
let socket: Socket;
class Socket {
  binaryType='';onopen:(()=>void)|null=null;onmessage:((event:{data:unknown})=>void)|null=null;onerror:(()=>void)|null=null;onclose:(()=>void)|null=null;
  sent:unknown[]=[];closed=false;
  constructor() { socket=this; }
  send(value:unknown) { this.sent.push(value); }
  close() { this.closed=true; }
  message(data:unknown) { this.onmessage?.({data}); }
}
const runtime: Runtime={id:'runtime',workspace_id:'workspace',revision_id:'revision',generation:1,profile_version:'gpu-v1',desired_state:'RUNNING',state:'READY',failure_detail:null,lifetime_deadline:null};
function page(){return render(<MemoryRouter initialEntries={['/interactive/workspace']}><Routes><Route path='/interactive/:id' element={<InteractiveDetails/>}/></Routes></MemoryRouter>);}
beforeEach(()=>{
  vi.resetAllMocks();vi.stubGlobal('WebSocket',Socket);
  vi.mocked(interactive.detail).mockResolvedValue({id:'workspace',name:'Runtime workspace',source_type:'UPLOAD',source_job_id:null,revision:{id:'revision',revision_number:1,origin:'UPLOAD',state:'IMAGE_READY',image_tag:null,image_digest_ref:null,failure_reason:null}});
  vi.mocked(interactive.logs).mockResolvedValue({lines:[],state:'IMAGE_READY'});
  vi.mocked(interactive.runtime).mockResolvedValue(runtime);
  vi.mocked(interactive.connection).mockResolvedValue({wss_url:'wss://gateway.example/v1/connect/resource/terminal',ticket:'private',expires_at:'later',runtime_id:'runtime',generation:1,protocol:'tcp-stream-v1',terminal_protocol:'terminal-stream-v1'});
});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
it('shows success after workload OPENED and CLOSE cleanup and keeps Stop available',async()=>{
  page();await screen.findByRole('heading',{name:'Runtime workspace'});
  fireEvent.click(screen.getByRole('button',{name:'Connect',exact:true}));
  await waitFor(()=>expect(socket).toBeTruthy());socket.onopen?.();
  expect(screen.queryByText('Connected successfully')).toBeNull();
  socket.message('{"type":"ready","protocol":"tcp-stream-v1"}');
  expect(screen.queryByText('Connected successfully')).toBeNull();
  socket.message(record(5,new TextEncoder().encode('{"session_id":"session","protocol":"terminal-stream-v1"}')));
  expect(new Uint8Array(socket.sent[2] as ArrayBuffer)[1]).toBe(4);
  socket.message(record(7,new TextEncoder().encode('{"code":0,"reason":"closed"}')));
  await screen.findByText('Connected successfully');expect(socket.closed).toBe(true);
  expect(interactive.stop).not.toHaveBeenCalled();expect((screen.getByRole('button',{name:'Stop'}) as HTMLButtonElement).disabled).toBe(false);
});
it('retries Start with its original key after a lost response',async()=>{
  vi.mocked(interactive.runtime).mockResolvedValue(null);
  vi.mocked(interactive.start).mockRejectedValueOnce(new Error('Network unavailable')).mockResolvedValueOnce({...runtime,state:'QUEUED'});
  page();await screen.findByRole('button',{name:'Start'});fireEvent.click(screen.getByRole('button',{name:'Start'}));
  await screen.findByText('Network unavailable');fireEvent.click(screen.getByRole('button',{name:'Start'}));
  await waitFor(()=>expect(interactive.start).toHaveBeenCalledTimes(2));
  expect(vi.mocked(interactive.start).mock.calls[0][1]).toBe(vi.mocked(interactive.start).mock.calls[1][1]);
});
it('closes the check on navigation/unmount without stopping the workload',async()=>{
  const view=page();await screen.findByRole('button',{name:'Connect',exact:true});
  fireEvent.click(screen.getByRole('button',{name:'Connect',exact:true}));
  await waitFor(()=>expect(interactive.connection).toHaveBeenCalledOnce());
  view.unmount();expect(socket.closed).toBe(true);expect(interactive.stop).not.toHaveBeenCalled();
});
