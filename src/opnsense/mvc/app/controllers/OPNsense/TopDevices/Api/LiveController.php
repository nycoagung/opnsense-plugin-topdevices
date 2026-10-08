<?php

/*
 * TopDevices live stream: per-device traffic rates as server-sent events.
 * The rates come from scripts/topdevices/live.py through configd; see
 * docs/superpowers/specs/2026-09-23-live-traffic-design.md.
 */

namespace OPNsense\TopDevices\Api;

use OPNsense\Base\ApiControllerBase;

class LiveController extends ApiControllerBase
{
    /**
     * GET /api/topdevices/live/stream/{interval}
     * @param string $interval seconds between samples, 1-10
     * @return array{status: string, message: string}|null an error, or null once
     *         the stream has been handed to the response
     */
    public function streamAction(string $interval = '1'): ?array
    {
        if (!ctype_digit($interval) || (int)$interval < 1 || (int)$interval > 10) {
            return ['status' => 'failed', 'message' => 'interval must be a whole number of seconds from 1 to 10'];
        }
        /* The relay ends a stream that stays silent longer than this poll
           timeout. live.py writes at least once per interval, keepalives
           included, except while one sample runs - and a very large state
           table can make a sample take seconds. The margin is for that: too
           tight, and every slow sample would drop the stream and start a
           reconnect storm of fresh samplers. */
        $this->configdStream(
            'topdevices live',
            [$interval],
            ['Content-Type: text/event-stream', 'Cache-Control: no-cache'],
            (int)$interval + 10
        );
        return null;
    }
}
