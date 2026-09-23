<?php

/*
 * TopDevices exact recent ranges: every device's totals, or one device's peers
 * and ports, for a window from NetFlow's raw flow log, through
 * scripts/topdevices/flows.py (configd). See
 * docs/superpowers/specs/2026-09-23-raw-flow-ranges-design.md
 */

namespace OPNsense\TopDevices\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\ACL;
use OPNsense\Core\Backend;

class FlowsController extends ApiControllerBase
{
    /* The same data reaches the dashboard through core's NetFlow exports; this
       endpoint must never show it to someone core itself would not. */
    private function mayReadNetflow()
    {
        return (new ACL())->isPageAccessible($this->getUserName(), '/api/diagnostics/networkinsight/export');
    }

    private function answer($action, $params)
    {
        $data = json_decode((string)(new Backend())->configdpRun($action, $params), true);
        return is_array($data) ? $data : ['error' => 'no answer from the flows script'];
    }

    /**
     * GET /api/topdevices/flows/totals/{from}/{to}
     * @param string $from epoch seconds
     * @param string $to epoch seconds
     */
    public function totalsAction($from = '', $to = '')
    {
        if (!$this->mayReadNetflow()) {
            return ['error' => 'this needs the Diagnostics: Network Insight privilege too'];
        }
        if (!ctype_digit((string)$from) || !ctype_digit((string)$to)) {
            return ['error' => 'from and to must be whole epoch seconds'];
        }
        return $this->answer('topdevices flows totals', [(string)$from, (string)$to]);
    }

    /**
     * GET /api/topdevices/flows/device/{ip}/{from}/{to}
     * @param string $ip the device's IPv4 address
     * @param string $from epoch seconds
     * @param string $to epoch seconds
     */
    public function deviceAction($ip = '', $from = '', $to = '')
    {
        if (!$this->mayReadNetflow()) {
            return ['error' => 'this needs the Diagnostics: Network Insight privilege too'];
        }
        if (filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) === false
            || !ctype_digit((string)$from) || !ctype_digit((string)$to)) {
            return ['error' => 'ip must be IPv4, and from and to whole epoch seconds'];
        }
        return $this->answer('topdevices flows device', [(string)$ip, (string)$from, (string)$to]);
    }
}
