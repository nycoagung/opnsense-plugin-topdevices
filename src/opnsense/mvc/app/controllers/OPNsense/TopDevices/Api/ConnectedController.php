<?php

/*
 * TopDevices connected devices: who is on the network now, and who was in the last
 * seven days, through scripts/topdevices/connected.py (configd). See
 * docs/superpowers/specs/2026-10-27-connected-devices-widget-design.md
 */

namespace OPNsense\TopDevices\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\ACL;
use OPNsense\Core\Backend;

class ConnectedController extends ApiControllerBase
{
    /**
     * GET /api/topdevices/connected/list: the script's answer, with the time zone set
     * under System: Settings: General, in which the widget shows its clock times.
     *
     * @return array<mixed>
     */
    public function listAction(): array
    {
        /* The same addresses and MACs are on core's ARP table page; this endpoint
           must never show them to someone core itself would not. */
        if (!(new ACL())->isPageAccessible($this->getUserName(), '/api/diagnostics/interface/get_arp')) {
            return ['status' => 'failed', 'message' => 'this needs the Diagnostics: ARP Table privilege too'];
        }
        /* configd can answer nothing at all, hence the cast */
        $data = json_decode((string)(new Backend())->configdRun('topdevices connected'), true);
        if (!is_array($data)) {
            $data = ['status' => 'failed', 'message' => 'could not read the host list'];
        }
        $data['zone'] = date_default_timezone_get();
        return $data;
    }
}
